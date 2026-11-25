#!/usr/bin/env node
import { createHash } from "node:crypto";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { dirname, isAbsolute, normalize } from "node:path";

const LOOPBACK = new Set(["127.0.0.1", "localhost", "[::1]", "::1"]);
const EDITOR_RE = /^https:\/\/www\.figma\.com\/(design|file)\/([A-Za-z0-9]+)/;

function parseArgs(argv) {
  const out = { host: "127.0.0.1", port: "9222", timeoutMs: "30000" };
  for (let i = 0; i < argv.length; i += 1) {
    const key = argv[i].replace(/^--/, "").replace(/-([a-z])/g, (_, c) => c.toUpperCase());
    const next = argv[i + 1];
    if (next === undefined || next.startsWith("--")) out[key] = true;
    else { out[key] = next; i += 1; }
  }
  return out;
}

function fail(message, code = 2) {
  console.error(`figma-rails: ${message}`);
  process.exit(code);
}

const args = parseArgs(process.argv.slice(2));
if (args.help || (!args.file && !args.probe && !args.targets && !args.fetch)) {
  console.log(`usage: cdp-eval.mjs --file-key <key> (--targets | --probe | --file <snippet.js> [--args '<json>'] | --fetch <runId>/<assetId> --out runs/<id>/<file>)
  --host 127.0.0.1 --port 9222   loopback CDP endpoint only
  --file-key <key>               Figma file key the tab must show (required except --targets)
  --target-id <id>               narrow to one tab; it must still be the editor for --file-key
  --file <snippet.js>            an expression, or a function taking --args (snippets/*.js)
  --fetch <runId>/<assetId>      read a staged export (snippets/stage-export.js) in chunks
  --out runs/<id>/<file>         relative path inside the run directory
  --timeout-ms 30000`);
  process.exit(args.help ? 0 : 2);
}
if (!LOOPBACK.has(args.host)) fail(`--host ${args.host} is not loopback; attach only to Chrome on this machine.`);

const base = `http://${args.host}:${args.port}`;
let targets;
try {
  targets = await (await fetch(`${base}/json/list`)).json();
} catch {
  fail(`no Chrome DevTools at ${base}. Start Chrome with --remote-debugging-address=127.0.0.1 --remote-debugging-port=${args.port} and a dedicated --user-data-dir.`);
}
const editors = targets
  .filter((t) => t.type === "page" && EDITOR_RE.test(t.url))
  .map((t) => ({ id: t.id, title: t.title, url: t.url, fileKey: t.url.match(EDITOR_RE)[2], ws: t.webSocketDebuggerUrl }));
if (args.targets) {
  console.log(JSON.stringify(editors.map(({ ws, ...t }) => t), null, 2));
  process.exit(0);
}
if (!args.fileKey) fail("--file-key is required so the command cannot run in the wrong file.");
const matches = editors.filter((t) => t.fileKey === args.fileKey && (!args.targetId || t.id === args.targetId));
if (matches.length !== 1) fail(`${matches.length} Figma editor tabs match file key ${args.fileKey}${args.targetId ? ` and target ${args.targetId}` : ""}; run --targets and pass --target-id.`, 3);
const target = matches[0];
const wsHost = new URL(target.ws).hostname;
if (!LOOPBACK.has(wsHost) && !LOOPBACK.has(`[${wsHost}]`)) fail(`webSocketDebuggerUrl host ${wsHost} is not loopback.`);

let expression = null;
if (args.fetch) {
  if (!args.out || isAbsolute(args.out) || normalize(args.out).startsWith("..")) fail("--out must be a relative path inside the run directory, e.g. runs/<commandId>/hero@2x.png.");
} else if (args.probe) {
  expression = readFileSync(new URL("./snippets/probe.js", import.meta.url), "utf8");
} else {
  const source = readFileSync(args.file, "utf8").trim();
  const isFunction = /^(async\s*)?(\([^)]*\)|[A-Za-z_$][\w$]*)\s*=>|^(async\s+)?function\b/.test(source);
  expression = isFunction ? `(async () => (${source})(${args.args ?? "{}"}))()` : source;
}

const ws = new WebSocket(target.ws);
let nextId = 0;
const pending = new Map();
const contexts = [];
ws.addEventListener("message", (event) => {
  const msg = JSON.parse(event.data);
  if (msg.method === "Runtime.executionContextCreated") contexts.push(msg.params.context);
  if (msg.id && pending.has(msg.id)) {
    const { resolve, reject } = pending.get(msg.id);
    pending.delete(msg.id);
    msg.error ? reject(new Error(msg.error.message)) : resolve(msg.result);
  }
});
const send = (method, params = {}) => new Promise((resolve, reject) => {
  nextId += 1;
  pending.set(nextId, { resolve, reject });
  ws.send(JSON.stringify({ id: nextId, method, params }));
});
await new Promise((resolve, reject) => {
  ws.addEventListener("open", resolve, { once: true });
  ws.addEventListener("error", () => reject(new Error("websocket error")), { once: true });
});
const timer = setTimeout(() => fail(`timed out after ${args.timeoutMs} ms`, 4), Number(args.timeoutMs) + 2000);
try {
  await send("Page.enable");
  await send("Runtime.enable");
  const { frameTree } = await send("Page.getFrameTree");
  const mainFrameId = frameTree.frame.id;
  const context = contexts.find((c) => c.auxData?.frameId === mainFrameId && c.auxData?.isDefault)
    ?? contexts.find((c) => c.auxData?.isDefault);
  if (!context) fail("no default execution context in the main frame; reload the tab and retry.", 4);
  const evaluate = async (expr) => {
    const res = await send("Runtime.evaluate", {
      expression: expr,
      ...(context.uniqueId ? { uniqueContextId: context.uniqueId } : { contextId: context.id }),
      includeCommandLineAPI: true,
      awaitPromise: true,
      returnByValue: true,
      objectGroup: "figma-rails",
      timeout: Number(args.timeoutMs),
    });
    if (res.exceptionDetails) fail(`runtime_exception: ${res.exceptionDetails.exception?.description ?? res.exceptionDetails.text}`, 5);
    return res.result.value;
  };
  if (args.fetch) {
    const [runId, assetId] = String(args.fetch).split("/");
    const ref = `globalThis.__figmaSinkBridge?.[${JSON.stringify(runId)}]?.[${JSON.stringify(assetId)}]`;
    const total = await evaluate(`(() => { const c = ${ref}; return Array.isArray(c) ? c.length : -1; })()`);
    if (total < 0) fail(`nothing staged under ${args.fetch}; run snippets/stage-export.js first.`, 5);
    let base64 = "";
    for (let i = 0; i < total; i += 1) base64 += await evaluate(`${ref}[${i}]`);
    const bytes = Buffer.from(base64, "base64");
    mkdirSync(dirname(args.out), { recursive: true });
    writeFileSync(args.out, bytes);
    await evaluate(`(() => { delete globalThis.__figmaSinkBridge[${JSON.stringify(runId)}][${JSON.stringify(assetId)}]; return true; })()`);
    console.log(JSON.stringify({ type: "binary", path: args.out, bytes: bytes.length, sha256: createHash("sha256").update(bytes).digest("hex") }, null, 2));
  } else {
    const value = await evaluate(expression);
    console.log(typeof value === "string" ? value : JSON.stringify(value, null, 2));
  }
} finally {
  clearTimeout(timer);
  ws.close();
}
