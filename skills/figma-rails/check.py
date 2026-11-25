import argparse
import json
import math
import re
import shlex
import struct
import sys
from pathlib import Path


class Finding:
    def __init__(self, rule, message, line=1):
        self.rule = rule
        self.message = message
        self.line = line

    def __repr__(self):
        return f"{self.rule}:{self.line}: {self.message}"


class _Kind:
    def __init__(self, rules, fn):
        self.rules = list(rules)
        self.fn = fn
        self.doc = (fn.__doc__ or "").strip()


class Checker:
    """Small standalone runner so check.py does not need the skillcheck package."""

    def __init__(self, name, covers=None):
        self.name = name
        self.covers = dict(covers or {})
        self.kinds = {}

    def kind(self, name, rules):
        def decorate(fn):
            self.kinds[name] = _Kind(rules, fn)
            return fn
        return decorate

    def run(self, text, kind):
        return list(self.kinds[kind].fn(text))

    def main(self, argv=None):
        parser = argparse.ArgumentParser(prog="check.py", description=f"{self.name} lint checker")
        parser.add_argument("file", nargs="?", help="plan, envelope, or snippet file to check")
        parser.add_argument("--kind", choices=sorted(self.kinds), help="which rule set to apply")
        parser.add_argument("--list", action="store_true", help="print every kind and rule")
        args = parser.parse_args(argv)
        if args.list:
            for name in sorted(self.kinds):
                spec = self.kinds[name]
                summary = spec.doc.split("\n", 1)[0] if spec.doc else ""
                print(f"{name}\t{summary}")
                for rule in spec.rules:
                    print(f"  {rule}")
            return 0
        if not args.file or not args.kind:
            parser.error("give a file and --kind, or pass --list")
        try:
            text = Path(args.file).read_text(encoding="utf-8")
        except OSError as err:
            print(f"check.py: {err}", file=sys.stderr)
            return 2
        found = self.run(text, args.kind)
        if not found:
            print("PASS")
            return 0
        for item in found:
            print(f"{item.rule}:{item.line}: {item.message}")
        return 1


CHECKER = Checker("figma-rails", covers={
    "fl-bind": r"bind (the )?(remote[- ]debugging|cdp)[^.]*127\.0\.0\.1",
    "fi-import": r"4096\s*px",
    "fi-export": r"32\s*(mp|megapixel)",
    "fa-locked": r"locked\s*===?\s*0",
})

LOOPBACK = {"127.0.0.1", "localhost", "::1", "[::1]"}
NODE_ID_RE = re.compile(r"^I?\d+:\d+(;\d+:\d+)*$")
URL_NODE_ID_RE = re.compile(r"^\d+-\d+$")
MAX_IMPORT_PX = 4096
MAX_EXPORT_PIXELS = 32_000_000
CDP_TRANSPORT = "chrome-cdp-runtime-evaluate"
CONNECTOR_TRANSPORT = "figma-mcp-use-figma"
TRANSPORTS = {CDP_TRANSPORT, CONNECTOR_TRANSPORT}
CONNECTOR_FILE_KEY_RE = re.compile(r"[0-9a-zA-Z]{22,128}")
UUID_RE = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
READ_ONLY_COMMANDS = {"probe", "extract", "list-pages", "export", "audit", "select-node", "capture"}
SNAPSHOT_SCHEMA = "figma-sink-snapshot/v3"
GRID_LAYOUT = ("gridRowCount", "gridColumnCount", "gridRowGap", "gridColumnGap", "gridRowSizes", "gridColumnSizes", "gridAutoTracks", "gridItemsPositioning", "gridRowSizingCSS", "gridColumnSizingCSS", "gridStyleId",
               "gridChildHorizontalAlign", "gridChildVerticalAlign", "gridRowAnchorIndex", "gridColumnAnchorIndex", "gridRowSpan", "gridColumnSpan")
FIELD_GROUPS = {
    "name": ("name",),
    "structure": ("type", "parentId", "depth", "index"),
    "geometry": ("x", "y", "width", "height", "relativeTransform"),
    "visibility": ("visible", "locked"),
    "characters": ("characters", "charactersDigest", "charactersLength"),
    "font": ("fontName", "fontSize", "fontStyle", "lineHeight", "letterSpacing", "textAlignHorizontal", "textAlignVertical", "textCase", "textDecoration", "textDecorationStyle", "textDecorationOffset",
             "textDecorationThickness", "textDecorationColor", "textDecorationSkipInk", "paragraphIndent", "paragraphSpacing", "listSpacing", "listOptions", "indentation", "leadingTrim", "hangingList",
             "hangingPunctuation", "openTypeFeatures"),
    "fill": ("fills", "fillStyleId"),
    "stroke": ("strokes", "strokeStyleId", "strokeWeight", "strokeTopWeight", "strokeRightWeight", "strokeBottomWeight", "strokeLeftWeight", "strokeAlign", "strokeCap", "strokeJoin", "strokeMiterLimit",
               "dashPattern", "complexStrokeProperties", "variableWidthStrokeProperties"),
    "textStyle": ("textStyleId",),
    "effect": ("effects", "effectStyleId"),
    "radius": ("cornerRadius", "topLeftRadius", "topRightRadius", "bottomRightRadius", "bottomLeftRadius", "cornerSmoothing"),
    "layout": ("layoutMode", "primaryAxisSizingMode", "counterAxisSizingMode", "paddingLeft", "paddingRight", "paddingTop", "paddingBottom", "itemSpacing", "counterAxisSpacing", "primaryAxisAlignItems", "counterAxisAlignItems", "layoutWrap",
               "counterAxisAlignContent", "itemReverseZIndex", "strokesIncludedInLayout", "minWidth", "maxWidth", "minHeight", "maxHeight", "textAutoResize",
               "layoutAlign", "layoutGrow", "layoutPositioning", "layoutSizingHorizontal", "layoutSizingVertical") + GRID_LAYOUT,
    "other": (),
}
EXPLICIT_GROUPS = ("font", "fill", "stroke", "textStyle", "effect", "radius", "layout", "other:<property>")
OTHER_GROUP_RE = re.compile(r"other:[A-Za-z_][A-Za-z0-9_]*")
GROUP_OF = {field: group for group, fields in FIELD_GROUPS.items() for field in fields}


def group_of(prop):
    return GROUP_OF.get(prop) or f"other:{prop}"


def is_plannable_group(group):
    return (group in FIELD_GROUPS and group != "other") or bool(OTHER_GROUP_RE.fullmatch(group))


def is_explicit_group(group):
    return group in EXPLICIT_GROUPS or group.startswith("other:")


FIGMA_DEFAULT_NAMES = {"frame", "group", "rectangle", "text", "vector", "ellipse", "component", "section", "image", "layer"}


def lines_of(text):
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.strip()
        if line and not line.startswith("#"):
            yield n, line


def joined_lines(text):
    out, buf, start = [], "", None
    for n, raw in enumerate(text.splitlines(), 1):
        line = raw.rstrip()
        if not buf and (not line.strip() or line.strip().startswith("#")):
            continue
        if start is None:
            start = n
        if line.endswith("\\"):
            buf += line[:-1] + " "
            continue
        out.append((start, (buf + line).strip()))
        buf, start = "", None
    if buf:
        out.append((start, buf.strip()))
    return out


def load_json(text, rule):
    try:
        data = json.loads(text, parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)))
    except (json.JSONDecodeError, ValueError) as err:
        return None, [Finding(rule, f"not valid finite JSON ({err}). Write the envelope with json.dumps, not by hand.", getattr(err, "lineno", 1))]
    if not isinstance(data, dict):
        return None, [Finding(rule, "the top level must be one JSON object.", 1)]
    return data, []


CHROME_RE = re.compile(r"--remote-debugging-port(=|\s)")
ADDRESS_RE = re.compile(r"--remote-debugging-address(?:=|\s+)(\S+)")
USER_DATA_RE = re.compile(r"--user-data-dir(?:=|\s+)(\"[^\"]+\"|'[^']+'|\S+)")
NORMAL_PROFILE_RE = re.compile(r"(?i)Library/Application Support/Google/Chrome/?$|\.config/(google-chrome|chromium)/?$|AppData[\\/]Local[\\/]Google[\\/]Chrome[\\/]User Data")
ENDPOINT_RE = re.compile(r"\b(?:https?|wss?)://([^/\s:'\"]+|\[[^\]]+\]):(\d+)/(?:json|devtools)")
EXPOSE_RE = re.compile(r"\bngrok\b|\bcloudflared\b|\bsocat\b.*TCP(4|6)?-LISTEN|\bssh\b.*\s-[A-Za-z]*[RgL]\b|\btailscale\s+(serve|funnel)\b|\bdevtunnel\b")
SECRET_RE = re.compile(r"Network\.get(All)?Cookies|Storage\.getCookies|document\.cookie|\bCookies\b(\.sqlite)?['\"]?\s*$|/Cookies\b|localStorage\.getItem|\bfigd_[A-Za-z0-9_-]{8,}|X-Figma-Token:\s*(?!\$)[A-Za-z0-9_-]{8,}")
HOST_RE = re.compile(r"--host(?:=|\s+)(\S+)")
KILL_RE = re.compile(r"\b(pkill|killall)\b[^|;&]*(?i:chrom)|\bkill\b.*\$\(\s*pgrep[^)]*(?i:chrom)")


@CHECKER.kind("launch", rules=["fl-bind", "fl-profile", "fl-origins", "fl-endpoint", "fl-expose", "fl-secret", "fl-kill"])
def launch(text):
    """Planned shell lines (Chrome launch, curl /json, port checks): loopback-only CDP, a dedicated --user-data-dir, no '*' origins, no tunnels, no cookie or token reads, no kill by process name."""
    found = []
    for n, line in joined_lines(text):
        if CHROME_RE.search(line):
            address = ADDRESS_RE.search(line)
            if not address:
                found.append(Finding("fl-bind", "Chrome is started with a debugging port but no --remote-debugging-address; add --remote-debugging-address=127.0.0.1 so the port never listens on a shared network.", n))
            elif address.group(1).strip("'\"") not in LOOPBACK:
                found.append(Finding("fl-bind", f"--remote-debugging-address={address.group(1)} exposes the logged-in Figma session to the network; use 127.0.0.1.", n))
            profile = USER_DATA_RE.search(line)
            if not profile:
                found.append(Finding("fl-profile", "Chrome with a debugging port needs its own --user-data-dir (e.g. ~/.figma-rails-chrome); never attach CDP to the user's everyday profile.", n))
            elif NORMAL_PROFILE_RE.search(profile.group(1).strip("'\"")):
                found.append(Finding("fl-profile", f"--user-data-dir {profile.group(1)} is the normal Chrome profile; use a dedicated automation profile directory.", n))
            if re.search(r"--profile-directory(=|\s+)['\"]?Default\b", line):
                found.append(Finding("fl-profile", "--profile-directory=Default points CDP at the everyday profile; drop it and use a dedicated --user-data-dir.", n))
        if re.search(r"--remote-allow-origins(=|\s+)['\"]?\*", line):
            found.append(Finding("fl-origins", "--remote-allow-origins=* lets any web page open the DevTools socket; omit it (CLI clients do not need it) or name one exact origin.", n))
        cli_host = HOST_RE.search(line)
        if cli_host and cli_host.group(1).strip("\"'") not in LOOPBACK:
            found.append(Finding("fl-endpoint", "the CDP --host must be loopback; run the client on Chrome's machine.", n))
        for host, port in ENDPOINT_RE.findall(line):
            if host not in LOOPBACK:
                found.append(Finding("fl-endpoint", f"CDP endpoint {host}:{port} is not loopback; attach only to http://127.0.0.1:<port> on the machine running Chrome.", n))
        if EXPOSE_RE.search(line):
            found.append(Finding("fl-expose", "this forwards or tunnels a port; never expose the DevTools port. Run the CDP client on the same machine as Chrome instead.", n))
        if SECRET_RE.search(line):
            found.append(Finding("fl-secret", "this reads or prints cookies, local storage or a Figma token; the approach needs none of them. Remove it; use the logged-in tab through Runtime.evaluate only.", n))
        if KILL_RE.search(line):
            found.append(Finding("fl-kill", "killing Chrome by name also kills other agents' browsers (a shared Chrome is not yours alone). Kill only the PID you started: kill \"$CHROME_PID\".", n))
    return found


DOM_RE = re.compile(r"document\.(querySelector(All)?|getElementsBy\w+|getElementById)|\.innerText\b|\.innerHTML\b|\.textContent\b")
LOCAL_PATH_RE = re.compile(r"/Users/|/home/|[A-Z]:\\\\|file://")
MUTATION_RE = re.compile(r"\.remove\(\)|\.appendChild\(|\.insertChild\(|figma\.create[A-Z]\w*\(|\.characters\s*=(?!=)|\.fills\s*=(?!=)|\.strokes\s*=(?!=)|\.fontName\s*=(?!=)|\.name\s*=(?!=)|\.resize\(|\.setPluginData\(|\.x\s*=(?!=)|\.y\s*=(?!=)")
STAGE_RE = re.compile(r"__figmaSinkBridge|stageExport|chunk", re.IGNORECASE)
NAME_FIND_RE = re.compile(r"\.(findAll|findOne|findChildren|findChild)\(.{0,200}?\.name\s*(===?|\.startsWith|\.includes|\.match)")


@CHECKER.kind("eval", rules=["fv-format", "fv-options", "fv-iife", "fv-dom", "fv-payload", "fv-local-path", "fv-write-guard", "fv-delete-by-name"])
def evaluate(text):
    """One Runtime.evaluate message or params JSON: figma-safe options, async IIFE, no DOM scraping, chunked exports, no local paths, guarded writes, no delete-by-name."""
    data, found = load_json(text, "fv-format")
    if data is None:
        return found
    params = data.get("params", data) if data.get("method", "Runtime.evaluate") == "Runtime.evaluate" else None
    if not isinstance(params, dict) or not isinstance(params.get("expression"), str) or not params["expression"].strip():
        return [Finding("fv-format", "expected a Runtime.evaluate message (or its params) with a non-empty 'expression' string.", 1)]
    expr = params["expression"]
    if "figma" in expr:
        missing = [k for k in ("includeCommandLineAPI", "awaitPromise") if params.get(k) is not True]
        if missing:
            found.append(Finding("fv-options", f"set {', '.join(f'{k}: true' for k in missing)}; without includeCommandLineAPI the figma global is undefined, and without awaitPromise async calls return a Promise handle.", 1))
    if re.search(r"\bawait\b", expr) and not re.match(r"\s*\(\s*async\b", expr):
        found.append(Finding("fv-iife", "the expression uses await but is not wrapped as (async () => { ... })(); wrap it so awaitPromise resolves the value.", 1))
    if DOM_RE.search(expr):
        found.append(Finding("fv-dom", "Figma is a canvas app; DOM text is not layer data. Read nodes through the figma Plugin API (getNodeByIdAsync, findAll, exportAsync).", 1))
    if "exportAsync" in expr and params.get("returnByValue") is True and not STAGE_RE.search(expr):
        found.append(Finding("fv-payload", "exportAsync bytes returned by value can break the CDP response; stage the base64 in globalThis.__figmaSinkBridge and read it back in chunks (snippets/stage-export.js).", 1))
    if LOCAL_PATH_RE.search(expr):
        found.append(Finding("fv-local-path", "a local file path inside the Figma runtime; the snippet returns bytes or metadata and the CLI process writes files.", 1))
    if MUTATION_RE.search(expr) and "dryRun" not in expr:
        found.append(Finding("fv-write-guard", "this snippet changes the document but never reads dryRun; return the plan unless command.dryRun === false and command.guards.allowMutation === true.", 1))
    if ".remove()" in expr and NAME_FIND_RE.search(expr) and "getNodeByIdAsync" not in expr:
        found.append(Finding("fv-delete-by-name", "nodes found by name are removed; Figma names repeat. Delete only ids from a prior run manifest, resolved with getNodeByIdAsync.", 1))
    return found


def check_node_id(value, field, rule):
    if isinstance(value, str) and URL_NODE_ID_RE.match(value):
        return [Finding(rule, f"{field} '{value}' is the URL form; the Plugin API id is '{value.replace('-', ':')}'.", 1)]
    if not isinstance(value, str) or not NODE_ID_RE.match(value):
        return [Finding(rule, f"{field} must be an explicit Plugin API node id like '712:538' (got {value!r}). Resolve the live selection to ids before planning.", 1)]
    return []


def entry_id(entry):
    return entry if isinstance(entry, str) else entry.get("id") if isinstance(entry, dict) else None


@CHECKER.kind("envelope", rules=["fe-format", "fe-transport", "fe-target", "fe-dry-run", "fe-guards", "fe-delete", "fe-readback", "fe-script"])
def envelope(text):
    """A figma-cli-command/v1 envelope: a known transport, explicit fileKey/pageId/nodeId, dryRun on by default, guards and counts for live writes, deletes only by id under a generated prefix, a readback scope, and per transport a registered script name (CDP) or a linted runs/<commandId>/*.js file (connector)."""
    data, found = load_json(text, "fe-format")
    if data is None:
        return found
    if data.get("schemaVersion") != "figma-cli-command/v1":
        found.append(Finding("fe-format", "schemaVersion must be 'figma-cli-command/v1'.", 1))
    for key in ("commandId", "command", "script"):
        if not isinstance(data.get(key), str) or not data[key]:
            found.append(Finding("fe-format", f"'{key}' must be a non-empty string.", 1))
    if not isinstance(data.get("dryRun"), bool):
        found.append(Finding("fe-format", "'dryRun' must be written out as true or false; true is the default for anything that can write.", 1))
    target, guards = data.get("target"), data.get("guards")
    if not isinstance(target, dict) or not isinstance(guards, dict):
        return found + [Finding("fe-format", "'target' and 'guards' must both be objects.", 1)]
    transport = data.get("transport")
    connector = transport == CONNECTOR_TRANSPORT
    if transport not in TRANSPORTS:
        found.append(Finding("fe-transport", f"transport {transport!r} is unknown; use '{CONNECTOR_TRANSPORT}' (Figma connector, the default) or '{CDP_TRANSPORT}' (CDP fallback).", 1))
    if not isinstance(target.get("fileKey"), str) or not re.fullmatch(r"[A-Za-z0-9]{10,}", target["fileKey"]):
        found.append(Finding("fe-target", "target.fileKey must be the key from the figma.com/design/<fileKey>/ URL; the tab is checked against it before running.", 1))
    elif connector and not CONNECTOR_FILE_KEY_RE.fullmatch(target["fileKey"]):
        found.append(Finding("fe-target", "use_figma takes a 22-128 character fileKey; copy it from the figma.com/design/<fileKey>/ URL or create_new_file, not a branch or short key.", 1))
    if connector:
        skills = {s.strip().removeprefix("resource:") for s in str(data.get("skillNames", "")).split(",")}
        if "figma-use" not in skills:
            found.append(Finding("fe-transport", "connector calls need skillNames with 'figma-use' (use_figma requires the figma-use guidance to be loaded first).", 1))
        if data.get("dryRun") is False and "figma-generate-design" not in skills:
            found.append(Finding("fe-transport", "a live connector write (create, edit or delete) is a visible change and needs 'figma-generate-design' in skillNames as well; load that skill before writing.", 1))
        if not isinstance(data.get("sessionId"), str) or not UUID_RE.fullmatch(data["sessionId"]):
            found.append(Finding("fe-transport", "connector commands carry the sessionId UUID from create_session; call create_session once per task and reuse it.", 1))
    found += check_node_id(target.get("pageId"), "target.pageId", "fe-target")
    found += check_node_id(target.get("nodeId"), "target.nodeId", "fe-target")
    if "rootNodeId" in target:
        found += check_node_id(target["rootNodeId"], "target.rootNodeId", "fe-target")
    command = data.get("command", "")
    live = data.get("dryRun") is False
    allow = guards.get("allowMutation") is True
    if command in READ_ONLY_COMMANDS and (allow or live):
        found.append(Finding("fe-dry-run", f"'{command}' is read-only; keep dryRun true and allowMutation false.", 1))
    elif live and not allow:
        found.append(Finding("fe-dry-run", "dryRun is false but guards.allowMutation is not true; a write needs both, so this run would silently do nothing or bypass the guard.", 1))
    allowed_ids = guards.get("allowedNodeIds", [])
    allowed_parents = guards.get("allowedParentIds", [])
    if not isinstance(allowed_ids, list) or not isinstance(allowed_parents, list):
        return found + [Finding("fe-format", "allowedNodeIds and allowedParentIds must be JSON lists, never strings or objects.", 1)]
    if not isinstance(guards.get("allowMutation"), bool):
        found.append(Finding("fe-guards", "guards.allowMutation must be a boolean; the default is false.", 1))
    if live:
        if not allowed_ids and not allowed_parents:
            found.append(Finding("fe-guards", "a live write needs guards.allowedNodeIds or guards.allowedParentIds; an empty scope means any node.", 1))
        for key in ("maxCreatedNodes", "maxUpdatedNodes", "maxDeletedNodes"):
            if not isinstance(guards.get(key), int) or isinstance(guards.get(key), bool) or guards[key] < 0:
                found.append(Finding("fe-guards", f"guards.{key} must be a whole number for a live write; it caps runaway loops.", 1))
    for value in list(allowed_ids) + list(allowed_parents):
        if value in ("*", "all", "selection") or not isinstance(value, str) or not NODE_ID_RE.match(value):
            found.append(Finding("fe-guards", f"guard id {value!r} is not an explicit node id; wildcards and live selection are not authorization.", 1))
    deletes = []
    for holder in (data.get("args"), data.get("plan")):
        if isinstance(holder, dict) and isinstance(holder.get("delete"), list):
            deletes += holder["delete"]
    max_deleted = guards.get("maxDeletedNodes", 0) if isinstance(guards.get("maxDeletedNodes"), int) else 0
    if deletes or max_deleted > 0:
        prefix = guards.get("generatedPrefix")
        if guards.get("requireExplicitIdsForDelete") is not True or guards.get("requireGeneratedPrefixForDelete") is not True:
            found.append(Finding("fe-delete", "a command that can delete needs requireExplicitIdsForDelete: true and requireGeneratedPrefixForDelete: true.", 1))
        if not isinstance(prefix, str) or len(prefix) < 4 or prefix.strip().lower() in FIGMA_DEFAULT_NAMES:
            found.append(Finding("fe-delete", f"generatedPrefix {prefix!r} is missing or too generic; use a marker like '__figma_rails__' that no designer layer starts with.", 1))
        for entry in deletes:
            node_id = entry_id(entry)
            name = entry.get("name") if isinstance(entry, dict) else None
            if not isinstance(node_id, str) or not NODE_ID_RE.match(node_id):
                found.append(Finding("fe-delete", f"delete entry {entry!r} has no explicit node id; never delete by name.", 1))
            elif node_id not in allowed_ids and not (isinstance(prefix, str) and prefix and isinstance(name, str) and name.startswith(prefix)):
                found.append(Finding("fe-delete", f"delete {node_id} is neither in guards.allowedNodeIds nor a generated node named with the prefix; only generated or explicitly named ids may go.", 1))
        if len(deletes) > max_deleted:
            found.append(Finding("fe-delete", f"{len(deletes)} planned deletes but guards.maxDeletedNodes is {max_deleted}.", 1))
    if live:
        steps = data.get("steps", [])
        required_steps = ["snapshot-before", "revalidate-target", "apply", "snapshot-after", "compare-plan"]
        if not isinstance(steps, list) or any(step not in steps for step in required_steps) or [step for step in steps if step in required_steps] != required_steps:
            found.append(Finding("fe-readback", "live writes need ordered steps: snapshot-before, revalidate-target, apply, snapshot-after, compare-plan. rootNodeId alone is not a readback step.", 1))
        expected = data.get("expectedChanges")
        if "rootNodeId" not in target or not isinstance(expected, dict) or not isinstance(expected.get("mutatedNodeIds"), list):
            found.append(Finding("fe-readback", "a live write needs target.rootNodeId (the before/after snapshot scope) and expectedChanges.mutatedNodeIds so the readback can prove nothing else changed.", 1))
        elif "mutatedProperties" in expected:
            props = expected["mutatedProperties"]
            if not isinstance(props, dict) or any(not isinstance(v, list) for v in props.values()):
                found.append(Finding("fe-readback", "expectedChanges.mutatedProperties maps a node id to a list of field groups, e.g. {\"12:3\": [\"characters\", \"font\"]}.", 1))
            else:
                for node_id, fields in props.items():
                    unknown = [f for f in fields if not isinstance(f, str) or not is_plannable_group(f)]
                    if node_id not in expected["mutatedNodeIds"]:
                        found.append(Finding("fe-readback", f"mutatedProperties names {node_id}, which is not in expectedChanges.mutatedNodeIds; plan the node first.", 1))
                    if unknown:
                        found.append(Finding("fe-readback", f"mutatedProperties[{node_id!r}] has unknown field groups {unknown}; use {', '.join(g for g in FIELD_GROUPS if g != 'other')}, or other:<property> for any other snapshot property (other:opacity, other:rotation).", 1))
        script = data.get("script", "")
        if not connector and isinstance(script, str) and not re.fullmatch(r"[A-Za-z][\w.-]*", script):
            found.append(Finding("fe-script", "live writes run a registered, reviewed script name, not inline code; inline snippets are for read-only probes.", 1))
    if connector:
        script, run_dir = data.get("script", ""), f"runs/{data.get('commandId', '')}/"
        if not isinstance(script, str) or not script.startswith(run_dir) or not script.endswith(".js") or ".." in Path(script).parts:
            found.append(Finding("fe-script", f"connector script must be the rendered file {run_dir}<name>.js that passed --kind code; send exactly that file's contents as use_figma code (code='$file:<abs path>' where the client supports it) so the linted bytes are what runs.", 1))
    return found


FIG_CREATE_RE = re.compile(r"\$fig\.(autoLayout|frame|text|rectangle|ellipse|polygon|star|line|vector|section|page|svg|svgComponent|group|union|subtract|intersect|exclude|component|variants|instance|varCollection|paintStyle|textStyle|effectStyle|clone)\(")
FIG_MUTATE_RE = re.compile(r"\$fig\.(set|delete|move|append|addAt|replace|reorder|gradient|image)\(|\.(set|remove|append|addAt|replace|moveTo|reorderChildren)\(")
RAW_CREATE_RE = re.compile(r"\bfigma\.create(?!Image\b)[A-Z]\w*\(|\bfigma\.createImage\(")
UNSUPPORTED_RE = re.compile(r"\bloadAllPagesAsync\b|\bsetPluginData\b|\bcreateImageAsync\b|\bfigma\.currentPage\s*=(?!=)")
IIFE_RE = re.compile(r"^\s*\(\s*(?:async\s*)?(?:\(\s*\)\s*=>|function\s*\(\s*\))", re.MULTILINE)
BROWSER_RE = re.compile(r"\b(document|window|location|localStorage|sessionStorage|navigator)\.")
RAW_FIND_RE = re.compile(r"\.(findAll|findOne|findAllWithCriteria|findChildren|findChild)\(")
COMMAND_DECL_RE = re.compile(r"^\s*const\s+command\s*=\s*\{", re.MULTILINE)
DRY_GATE_RE = re.compile(r"command\.dryRun\s*!==\s*false")
ALLOW_GATE_RE = re.compile(r"command\.guards\.allowMutation\s*!==\s*true")
NODE_READ_RE = re.compile(r"\b\w+\.node\b(?!\s*=[^=])")
FLUSH_RE = re.compile(r"await\s+\$fig\.done\(\s*\)")
NAME_SELECT_RE = re.compile(r"\$fig\.query\([^)]*name|\.(findAll|findOne)\(.{0,200}?\.name\b", re.DOTALL)
DELETE_RE = re.compile(r"\.remove\(\s*\)|\$fig\.delete\(")
MAX_CODE_CHARS = 50000


def strip_js_comments(code):
    return re.sub(r"(?m)^\s*//.*$", "", re.sub(r"/\*.*?\*/", "", code, flags=re.DOTALL))


@CHECKER.kind("code", rules=["fc-format", "fc-unsupported", "fc-raw-create", "fc-iife", "fc-browser", "fc-local-path", "fc-find", "fc-write-guard", "fc-prefix", "fc-flush", "fc-delete-by-name"])
def code(text):
    """The exact JS sent as use_figma `code` (connector transport, rendered by connector.py): $fig for creation, no APIs the connector rejects, no IIFE wrapper or browser globals, $fig.query instead of find*, every mutation behind the command.dryRun/allowMutation gate, created nodes named with the generated prefix, .node only after $fig.done(), deletes by id."""
    src = strip_js_comments(text)
    if not src.strip():
        return [Finding("fc-format", "the use_figma code is empty; render it with connector.py render.", 1)]
    found = []
    if len(text) > MAX_CODE_CHARS:
        found.append(Finding("fc-format", f"{len(text)} characters is over use_figma's {MAX_CODE_CHARS}-character code limit; split the work into retry-safe calls.", 1))
    line = lambda pos: src.count("\n", 0, pos) + 1
    m = UNSUPPORTED_RE.search(src)
    if m:
        found.append(Finding("fc-unsupported", f"'{m.group(0).strip()}' is not supported by use_figma (no loadAllPagesAsync, setPluginData or createImageAsync; switch pages with await figma.setCurrentPageAsync(page)).", line(m.start())))
    m = RAW_CREATE_RE.search(src)
    if m:
        found.append(Finding("fc-raw-create", f"'{m.group(0)}' creates nodes with the raw API; in use_figma every node is created through $fig (e.g. $fig.autoLayout, $fig.text), which batches, preloads fonts and returns the created ids.", line(m.start())))
    m = IIFE_RE.search(src)
    if m:
        found.append(Finding("fc-iife", "a bare (async () => {...})() statement: use_figma already wraps the code in an async function, so its value is lost. Write top-level await and a top-level return.", line(m.start())))
    m = BROWSER_RE.search(src) or DOM_RE.search(src)
    if m:
        found.append(Finding("fc-browser", f"'{m.group(0)}' is a browser global; use_figma runs on Figma's server with only figma and $fig. Read layers with $fig.query or figma.getNodeByIdAsync.", line(m.start())))
    m = LOCAL_PATH_RE.search(src)
    if m:
        found.append(Finding("fc-local-path", "a local file path inside the Figma runtime; return data from the code and let the local process write files.", line(m.start())))
    m = RAW_FIND_RE.search(src)
    if m:
        found.append(Finding("fc-find", f"'{m.group(0)}' searches with the raw API; use $fig.query('TYPE[name=...]', scope) or figma.getNodeByIdAsync(id).", line(m.start())))
    creates = list(FIG_CREATE_RE.finditer(src))
    mutations = sorted(creates + list(FIG_MUTATE_RE.finditer(src)) + list(RAW_CREATE_RE.finditer(src)) + list(MUTATION_RE.finditer(src)), key=lambda x: x.start())
    if mutations:
        first = mutations[0].start()
        decl = COMMAND_DECL_RE.search(src)
        gate = DRY_GATE_RE.search(src)
        allow = ALLOW_GATE_RE.search(src)
        if not decl or decl.start() > first or not gate or gate.start() > first or not allow or allow.start() > first:
            found.append(Finding("fc-write-guard", f"the code changes the file ('{mutations[0].group(0)}') before checking the command; render it with connector.py render (it declares const command) and return the plan first: if (command.dryRun !== false || command.guards.allowMutation !== true) return {{ plan }}.", line(first)))
    if creates:
        named = re.search(r"name\s*:\s*(`\$\{\s*command\.guards\.generatedPrefix\s*\}|command\.guards\.generatedPrefix\s*\+|['\"`]__figma_(?:sink|rails)__)", src)
        if not named:
            found.append(Finding("fc-prefix", "nodes are created without a generated-prefix name; name each top-level created node `${command.guards.generatedPrefix} <role>` so cleanup can find and delete only what this run made.", line(creates[0].start())))
    read = NODE_READ_RE.search(src)
    if read and FIG_CREATE_RE.search(src):
        flush = FLUSH_RE.search(src)
        if not flush or flush.start() > read.start():
            found.append(Finding("fc-flush", f"'{read.group(0)}' reads a plan node before await $fig.done(); .node is null until the plan is flushed, so the returned ids would be null.", line(read.start())))
    if DELETE_RE.search(src) and NAME_SELECT_RE.search(src):
        found.append(Finding("fc-delete-by-name", "nodes are deleted after a name search; Figma names repeat. Delete only ids from the run manifest with $fig.get('<id>').remove() or $fig.delete('<id>').", line(DELETE_RE.search(src).start())))
    return found


CHANGE_TO_PLAN = {"updated": "update", "deleted": "delete", "reparented": "reparent"}


@CHECKER.kind("result", rules=["fr-format", "fr-dry-run", "fr-readback", "fr-plan", "fr-artifacts"])
def result(text):
    """A figma-cli-result/v1 envelope: dry runs changed nothing, ok results carry a passing readback, every change was planned, artifacts are hashed relative paths."""
    data, found = load_json(text, "fr-format")
    if data is None:
        return found
    if data.get("schemaVersion") != "figma-cli-result/v1" or not isinstance(data.get("ok"), bool) or not isinstance(data.get("status"), str):
        found.append(Finding("fr-format", "needs schemaVersion 'figma-cli-result/v1', ok (bool) and status (string).", 1))
    plan = data.get("plan") if isinstance(data.get("plan"), dict) else {}
    changes = data.get("changes") if isinstance(data.get("changes"), dict) else {}
    applied = {k: v for k, v in changes.items() if k != "selection" and isinstance(v, list) and v}
    if data.get("status") in ("dry-run", "dry_run") and applied:
        found.append(Finding("fr-dry-run", f"a dry run reports applied changes ({', '.join(sorted(applied))}); that is a dry_run_violation. Stop and inspect before/after snapshots; do not attempt an automatic rollback.", 1))
    verification = data.get("verification")
    if data.get("ok") is True and data.get("status") != "dry-run":
        if not isinstance(verification, dict) or verification.get("ok") is not True:
            found.append(Finding("fr-readback", "ok is true without a passing readback; re-read the same scope after the command and set verification.ok from the diff, not from the command's own report.", 1))
        elif verification.get("unexpectedChanges"):
            found.append(Finding("fr-readback", "verification lists unexpectedChanges but ok is true; any unplanned change fails the run.", 1))
        elif verification.get("source") == "snapshot-diff" and verification.get("snapshotSchema") != SNAPSHOT_SCHEMA:
            found.append(Finding("fr-readback", f"the readback diffed {verification.get('snapshotSchema') or 'figma-sink-snapshot/v1'} snapshots, which record only a hand-picked field list, so a change to opacity, rotation, blend mode, constraints or any new property would pass; re-snapshot with the current snippets/snapshot.js ({SNAPSHOT_SCHEMA}) and rebuild the result with connector.py result.", 1))
    for group, keys in ((plan, ("create", "update", "delete", "reparent")), (changes, ("created", "updated", "deleted", "reparented", "selection"))):
        if any(not isinstance(group.get(key, []), list) for key in keys):
            return found + [Finding("fr-format", "plan and changes entries must be lists.", 1)]
    if not isinstance(data.get("artifacts", []), list):
        return found + [Finding("fr-format", "artifacts must be a list.", 1)]
    if data.get("status") == "passed" and (not isinstance(data.get("plan"), dict) or not isinstance(data.get("changes"), dict)):
        found.append(Finding("fr-format", "passed results need plan and changes objects to compare.", 1))
    for change_key, plan_key in CHANGE_TO_PLAN.items():
        planned = {entry_id(e) for e in plan.get(plan_key, []) if entry_id(e)}
        for entry in changes.get(change_key, []) or []:
            if entry_id(entry) not in planned:
                found.append(Finding("fr-plan", f"{change_key} {entry_id(entry)!r} is not in plan.{plan_key}; a change outside the plan fails verification.", 1))
    if len(changes.get("created", []) or []) > len(plan.get("create", []) or []):
        found.append(Finding("fr-plan", "more nodes were created than plan.create lists.", 1))
    for artifact in data.get("artifacts", []) or []:
        path = artifact.get("path", "") if isinstance(artifact, dict) else ""
        digest = artifact.get("sha256", "") if isinstance(artifact, dict) else ""
        if not path or path.startswith(("/", "~")) or ".." in Path(path).parts or re.match(r"[A-Za-z]:", path):
            found.append(Finding("fr-artifacts", f"artifact path {path!r} must be relative inside the run directory (runs/<commandId>/...).", 1))
        if not re.fullmatch(r"(sha256:)?[0-9a-f]{64}", str(digest)):
            found.append(Finding("fr-artifacts", f"artifact {path!r} needs its real sha256 (64 hex chars), computed from the written file.", 1))
    return found


REFERENCE_NAME_RE = re.compile(r"visual reference|reference slice|tracing", re.IGNORECASE)
SCREENSHOT_NAME_RE = re.compile(r"screenshot|full.?page|reference", re.IGNORECASE)
ICON_TEXT_RE = re.compile(r"^(?:(?:play_arrow|arrow_outward|keyboard_arrow_down|keyboard_arrow_up|expand_more|expand_less|chevron_right|chevron_left)(?:\s|$)|(?:add|menu|close|search|check)$)", re.IGNORECASE)
SECTION_NAME_RE = re.compile(r"^\d\d\s")
TOLERANCE = 1.0


def box(node):
    try:
        values = tuple(float(node[key]) for key in ("x", "y", "width", "height"))
        if any(not math.isfinite(v) for v in values) or values[2] < 0 or values[3] < 0:
            return None
        return values
    except (KeyError, TypeError, ValueError):
        return None


@CHECKER.kind("audit", rules=["fa-format", "fa-locked", "fa-reference", "fa-icon-text", "fa-bounds", "fa-sections", "fa-editable"])
def audit(text):
    """The JSON printed by snippets/audit.js for the final frame: no locked layers, no reference or screenshot-only layers, no icon-font text, every visible node inside the root frame, aligned numbered sections and real text in webpage mode."""
    data, found = load_json(text, "fa-format")
    if data is None:
        return found
    root, nodes = data.get("root"), data.get("nodes")
    if data.get("schemaVersion") != "figma-sink-audit/v1" or not isinstance(root, dict) or box(root) is None or not isinstance(nodes, list):
        return found + [Finding("fa-format", "expected the output of snippets/audit.js: schemaVersion 'figma-sink-audit/v1', root {id,name,x,y,width,height} and a nodes list.", 1)]
    if box(root)[2] <= 0 or box(root)[3] <= 0:
        return found + [Finding("fa-format", "root frame dimensions must be positive.", 1)]
    for node in nodes:
        if not isinstance(node, dict) or box(node) is None or type(node.get("locked")) is not bool or type(node.get("visible")) is not bool or type(node.get("depth")) is not int or not isinstance(node.get("name"), str) or not isinstance(node.get("characters", ""), str):
            return found + [Finding("fa-format", "each audit node needs finite x/y/width/height, integer depth, name, boolean visible/locked and string characters if present. Re-run snippets/audit.js; do not submit a partial or aggregate audit.", 1)]
    rx, ry, rw, rh = box(root)
    if root.get("locked"):
        found.append(Finding("fa-locked", f"the root frame {root.get('name')!r} is locked; unlock it before handoff.", 1))
    texts = 0
    for node in nodes:
        if not isinstance(node, dict) or node.get("visible") is False:
            continue
        label = f"{node.get('name')!r} ({node.get('id')})"
        if node.get("locked"):
            found.append(Finding("fa-locked", f"{label} is locked; the final file needs locked === 0 unless the user asked for a locked layer.", 1))
        if REFERENCE_NAME_RE.search(node.get("name") or "") or (node.get("imageFill") and SCREENSHOT_NAME_RE.search(node.get("name") or "")):
            found.append(Finding("fa-reference", f"{label} is a reference or screenshot layer; rebuild it as editable nodes or real media and delete the reference.", 1))
        if node.get("type") == "TEXT":
            texts += 1
            if ICON_TEXT_RE.search((node.get("characters") or "").strip()):
                found.append(Finding("fa-icon-text", f"{label} text {node.get('characters')!r} is a leaked icon-font name; replace it with a vector icon and the real label.", 1))
        b = box(node)
        if b and node.get("depth", 1) >= 1:
            x, y, w, h = b
            if x < rx - TOLERANCE or y < ry - TOLERANCE or x + w > rx + rw + TOLERANCE or y + h > ry + rh + TOLERANCE:
                found.append(Finding("fa-bounds", f"{label} at ({x:g},{y:g},{w:g}x{h:g}) falls outside the root frame ({rx:g},{ry:g},{rw:g}x{rh:g}); move or clip it inside the frame.", 1))
        if data.get("mode") == "webpage" and node.get("depth") == 1 and node.get("type") == "FRAME" and SECTION_NAME_RE.match(node.get("name") or "") and b and round(b[0]) != round(rx):
            found.append(Finding("fa-sections", f"section {label} starts at x={b[0]:g}, not the page edge x={rx:g}; align top-level sections to the page frame.", 1))
    if data.get("mode") == "webpage" and texts == 0:
        found.append(Finding("fa-editable", "the webpage frame has no TEXT nodes, so it is a screenshot, not an editable reconstruction.", 1))
    return found


DIM_RE = re.compile(r"^(\d+)\s*[x×]\s*(\d+)$")


def image_size(path):
    try:
        with open(path, "rb") as f:
            head = f.read(32)
            if head[:8] == b"\x89PNG\r\n\x1a\n":
                return struct.unpack(">II", head[16:24])
            if head[:6] in (b"GIF87a", b"GIF89a"):
                return struct.unpack("<HH", head[6:10])
            if head[:2] == b"\xff\xd8":
                f.seek(2)
                while True:
                    marker = f.read(2)
                    if len(marker) < 2 or marker[0] != 0xFF:
                        return None
                    size = struct.unpack(">H", f.read(2))[0]
                    if marker[1] in (0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7, 0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF):
                        h, w = struct.unpack(">xHH", f.read(5))
                        return w, h
                    f.seek(size - 2, 1)
    except (OSError, struct.error, ValueError):
        return None
    return None


@CHECKER.kind("image", rules=["fi-format", "fi-import", "fi-export"])
def image(text):
    """Planned bitmap moves, one per line: 'import <W>x<H>|<file> [label]' (figma.createImage, max 4096 px per side, PNG/JPEG/GIF) or 'export <W>x<H> [@scale] [label]' (REST render, max 32 MP after scale)."""
    found = []
    for n, line in lines_of(text):
        try:
            parts = shlex.split(line)
        except ValueError:
            parts = line.split()
        if len(parts) < 2 or parts[0] not in ("import", "export"):
            found.append(Finding("fi-format", "each line is 'import <W>x<H>|<file> [label]' or 'export <W>x<H> [@scale] [label]'.", n))
            continue
        op, size = parts[0], parts[1]
        dims = DIM_RE.match(size)
        if dims:
            w, h = int(dims.group(1)), int(dims.group(2))
        elif op == "import" and image_size(size):
            w, h = image_size(size)
            if not re.search(r"\.(png|jpe?g|gif)$", size, re.IGNORECASE):
                found.append(Finding("fi-format", f"{size}: figma.createImage takes PNG, JPEG or GIF only.", n))
        else:
            found.append(Finding("fi-format", f"'{size}' is neither <W>x<H> nor a readable PNG/JPEG/GIF file.", n))
            continue
        if w <= 0 or h <= 0:
            found.append(Finding("fi-format", "image width and height must be positive integers.", n))
            continue
        if op == "import" and max(w, h) > MAX_IMPORT_PX:
            bands = math.ceil(h / MAX_IMPORT_PX) * math.ceil(w / MAX_IMPORT_PX)
            found.append(Finding("fi-import", f"{w}x{h} is over the {MAX_IMPORT_PX}px createImage limit ('Image is too large'); split it into {bands} tiles of at most {MAX_IMPORT_PX}px and place each as its own image fill.", n))
        if op == "export":
            scale = 1.0
            for p in parts[2:]:
                if p.startswith("@"):
                    try:
                        scale = float(p[1:].rstrip("x"))
                    except ValueError:
                        found.append(Finding("fi-format", f"scale '{p}' is not a number like @2.", n))
            if not math.isfinite(scale) or scale <= 0:
                found.append(Finding("fi-format", "export scale must be positive and finite (e.g. @2).", n))
                continue
            pixels = w * h * scale * scale
            if pixels > MAX_EXPORT_PIXELS:
                best = math.floor(math.sqrt(MAX_EXPORT_PIXELS / (w * h)) * 100) / 100
                found.append(Finding("fi-export", f"{w}x{h} at @{scale:g} is {pixels / 1e6:.1f} MP; Figma scales renders over 32 MP down. Use @{best:g} or smaller, or export smaller nodes.", n))
    return found


if __name__ == "__main__":
    sys.exit(CHECKER.main())
