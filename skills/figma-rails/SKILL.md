---
name: figma-rails
description: >-
  Use when reading or editing a Figma file with Plugin API code and the result
  has to be constrained and proven: through the Figma connector's use_figma
  (default) or, as a fallback, raw CDP into a logged-in Figma tab. Also when
  matching a build or a webpage copy to a Figma frame that way.
---
# Figma rails (linted Plugin API runs with readback)

The value is the constraint, not the transport: every run is linted before it is sent, and judged by a before/after snapshot diff, not by what the code says it did. The checker, runner and snippets live in this checkout at `skills/figma-rails/`; run commands from the repository root.

## Checker
Run `python3 skills/figma-rails/check.py <file> --kind <kind>` until it prints PASS (`--list` shows every rule):
- `envelope`: the figma-cli-command/v1 JSON (`transport`, target, guards, dryRun, steps) before any run.
- `code`: the exact JS sent to use_figma. `skills/figma-rails/connector.py render` runs it for you and refuses to write a failing file.
- `result`: the figma-cli-result/v1 from `skills/figma-rails/connector.py result`.
- `image`: one line per bitmap moved; `audit`: the output of `snippets/audit.js`.
- CDP only: `launch` for Chrome and curl lines, `eval` for a hand-written Runtime.evaluate message.

## Pick the transport
- Connector (`figma-mcp-use-figma`): the default. Needs the Figma MCP connector signed in and the figma-use skill loaded, plus figma-generate-design for every live write (create, edit or delete); the envelope check enforces both.
- CDP (`chrome-cdp-runtime-evaluate`): only for the live selection or viewport of a tab the user has open, or when the connector is down or rate-limited. Managed machines may block loopback DevTools (Chrome policy LoopbackNetworkBlockedForUrls), so CDP works only where it is allowed.
- File JSON or image exports alone: the REST API. A durable two-way tool for the user: a Figma plugin.

## Launch
- Connector: `create_session` once per task with the fileKey; reuse its sessionId. New test work goes in a new file (`create_new_file`), never a file someone is working in.
- CDP: start your own Chrome with a dedicated profile and a loopback-only port, open the exact `figma.com/design/<fileKey>/` URL, keep `CHROME_PID=$!`. The user logs in there; never copy cookies or tokens. Attach to someone else's Chrome only when the user says that tab is the one.

## Doctor (read-only; first, and after anything surprising)
- Connector: `whoami` (account, plan, seat), `get_metadata` on the page, then `connector.py render snippets/doctor.js` sent through use_figma. `figma.root.name` reads "Document" there, so check the fileKey, not the name.
- CDP: `node skills/figma-rails/cdp-eval.mjs --port <port> --targets`, then `--file-key <key> --probe` must show `typeofFigma: "object"` and the expected file and page. A login, request-access or file-browser tab: stop and ask.

## Drive
1. Write the envelope and check it. Plan every node in `expectedChanges.mutatedNodeIds`, and list its field groups in `expectedChanges.mutatedProperties` (`{"12:3": ["characters"]}`; groups: name, structure, geometry, visibility, characters, font, fill, stroke, textStyle). A planned node without an entry may change anything except font, fill, stroke and textStyle.
2. Snapshot: render `snippets/snapshot.js` with `{"rootId": "<target.rootNodeId>"}`, run it, save `before.json`. If use_figma wrote the result to a file (write_to_file), `connector.py ingest <that file> -o before.json` unwraps it (`--returned` does the same for a return value); `result` also reads wrapped files directly.
3. Dry run: render with the `dryRun: true` envelope to `-o runs/<commandId>/dry-run/<name>.js` (render refuses any other path and saves the envelope as `dry-run/command.json`). Connector code uses `$fig` for nodes and starts with the gate `if (command.dryRun !== false || command.guards.allowMutation !== true) return { plan }`.
4. Send that exact file: use_figma with `code: "$file:<abs path>"`, `skillNames` from the envelope and the sessionId. Save the return as `dry-run/returned.json`, show the plan, and wait for the user.
5. Apply: flip only `dryRun` to false and `guards.allowMutation` to true, render to `runs/<commandId>/<name>.js`, and send it; save `returned.json`. Render refuses a live envelope that plans anything else than the dry run. Snapshot again right before applying; stop if the target changed.
6. Snapshot `after.json`, then `connector.py result --command ... --before ... --after ... --reported returned.json --artifact ... -o result.json`. It fails on any change the envelope did not plan, including side effects such as a hugging parent that resized or a font or fill that changed. `--reported` compares `createdNodeIds`, `updatedNodeIds` and `deletedNodeIds` (or use_figma's own `created`/`updated`/`deleted` result) with the readback and fails when the code's report missed a change or claimed one that did not land. `warnings` flags a text layer whose auto name no longer matches its text; it does not fail the run.
- On a use_figma error follow `safeToRetryWithoutCanvasRead`; when false, snapshot before retrying. Empty or unsupported responses are terminal.
- Work by ids. URL `node-id=712-538` is Plugin API `712:538`. Names repeat: when one matches twice, return id, page and size for each and let the user pick.
- CDP reads use `cdp-eval.mjs --file-key <key> --file <snippet.js> --args '<json>'`; bitmaps out via `snippets/stage-export.js` and `--fetch`.
- Re-runs read the current state and apply only the difference. CDP text edits load every font in the node's ranges first and fail instead of substituting a font.

## Evidence
Keep `runs/<commandId>/`: command.json, the rendered .js, returned.json, before.json, after.json, `dry-run/` (command.json, .js, returned.json), screenshots and result.json with sha256s. Connector screenshots: `get_screenshot` on the node and save the PNG it links to; treat that URL as a secret. `verification.ok` comes from the diff. Cleanup never deletes evidence.

What the readback compares (`figma-sink-snapshot/v2`; v1 snapshots are refused): name, type, parent, absolute box, visible, locked, text (first 200 characters); for text, fontName, fontSize, lineHeight, letterSpacing, horizontal and vertical alignment and textStyleId, with per-range values when they are mixed; for any node with paints, fills and strokes (type, visibility, opacity, blend, solid color, gradient stops and transform, image or video hash and scale mode, bound color variable), fill and stroke style ids and stroke weight. It does not see text decoration or case, effects, node opacity or blend mode, corner radius, auto-layout settings, constraints, rotation, component properties, variable values, image pixels behind an unchanged hash, or characters past 200 and ranges past 50 per node. Check those with get_screenshot.

## Design fidelity loop
When building from a Figma frame, the frame is the contract: CSS Modules with `globals.css` tokens, near pixel-exact, real images exported from Figma, frame bounds untouched unless asked.
1. Export the source frame (PNG and node JSON). Render the build at the same viewport, device scale, theme and state, with fonts and images loaded and animation frozen.
2. Compare the whole frame and each region. Record every mismatch with category, node id, expected, actual and delta.
3. Fix and repeat until no blocking mismatch is left or the user waives one with a reason. Report a gap you could not close; never ship a silent approximation.

## Webpage to Figma
- Distrust one full-page screenshot when the page has sticky, scroll-animated, lazy or carousel sections. Capture real viewport states at chosen scroll positions and inspect those sections before importing.
- Real media (photos, video posters, dense product UI, logos) become image fills. Copy, cards, forms, grids, navigation and footer become editable text and frames. Reference images are temporary QA layers.
- Structure it like a designer: one page frame, numbered section frames in reading order (`03 Social proof`), layers named by role, and a separate mobile frame when the layout changes.
- Before handoff, run `snippets/audit.js` with `{"frameId": "<id>", "mode": "webpage"}`, check its output with `--kind audit`, and screenshot focused slices for a visual pass. The audit checks structure, not looks.

## Cleanup
Delete only ids from your run manifest under the generated prefix, through a cleanup envelope with `args.delete`, and verify it with `connector.py result`. CDP: stop only the Chrome PID you started and leave the user's tabs as they were.
