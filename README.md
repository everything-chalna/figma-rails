# Figma Rails

## Why

The constraints are the point, not the transport. Plugin API code and the actual file result often did not match. I added lint before the code ran, and a before/after readback after it, and that made a huge quality difference.

Figma Rails is a constraint layer for Figma Plugin API runs. The official Figma connector can already execute code in a file through `use_figma`. A connector only runs that code. Figma Rails lints the code before it is sent, then judges a before/after snapshot of the document instead of the code's own report.

That difference is the reason this project exists next to the connector. Connector results often look successful while the file does not match the plan. Readback catches those mismatches. A text edit that reports a single text node can still resize a hugging parent frame; if that parent was not in the plan, the snapshot diff fails the run even though the code never mentioned it.

Figma Rails is not an MCP server, a packaged CLI, or a Figma plugin. The working method is the agent skill in `skills/figma-rails/`: the judgment calls in `SKILL.md`, plus a lint checker and a small connector runner.

## Quick start

From the repository root, with the Figma connector signed in:

1. Write a `figma-cli-command/v1` envelope. Set `"transport": "figma-mcp-use-figma"`, the file key, the `create_session` id, and `skillNames` (`figma-use`, plus `figma-generate-design` for any live write). `dryRun` defaults to true. List each node you will change in `expectedChanges.mutatedNodeIds`, and its field groups in `expectedChanges.mutatedProperties` (for example `{"12:3": ["characters", "font"]}`, or `{"12:3": ["other:opacity"]}` for a property outside the named groups).
2. Lint the envelope, then render the snippet. Rendering refuses to write a file that fails the `code` rules.

```sh
python3 skills/figma-rails/check.py runs/demo/command.json --kind envelope
python3 skills/figma-rails/connector.py render skills/figma-rails/snippets/snapshot.js \
  --args '{"rootId": "0:1"}' -o runs/demo/snapshot.js
```

3. Dry-run first: render the `dryRun: true` envelope to `runs/<commandId>/dry-run/<name>.js` and send it. The live render goes to `runs/<commandId>/<name>.js`, keeps the dry-run files, and refuses an envelope that plans anything the dry run did not.
4. Send the rendered file unchanged as `use_figma` code. Snapshot the same scope before and after. `connector.py result` diffs those snapshots into a `figma-cli-result/v1` and fails on any create, update, or delete the envelope did not plan, including a parent frame that resized and any property group that changed on a node whose plan did not list it, from a font or fill to opacity or rotation. `use_figma` refuses a return value over 48 KB, so a snapshot of more than a few dozen nodes comes in pages: when the snapshot says `"pages"` above 1, run it again with `"page": 1`, `2` and so on, with no write in between. `connector.py ingest <every page file> -o before.json` unwraps and merges them, and refuses pages from two different canvas states; `result` reads a one-page file, wrapped or plain, directly.

## Transports

Both transports use the same envelope, the same lint, and the same readback.

| Transport | When |
| --- | --- |
| Figma connector (`figma-mcp-use-figma`) | Default. `use_figma` runs the linted code on Figma's side. No browser and no debugging port. |
| Raw CDP (`chrome-cdp-runtime-evaluate`) | Fallback. Use it for the live selection or viewport of a tab the user already has open, or when the connector is down or rate-limited. |

CDP evaluates Plugin API code inside an already-open, logged-in Figma tab. Start a dedicated Chrome profile with the debugging port bound to `127.0.0.1` (`~/.figma-rails-chrome`), open the exact `figma.com/design/<fileKey>/` URL, and let the user sign in there. Managed machines may block loopback DevTools, which is why the connector is the default. The runner is `node skills/figma-rails/cdp-eval.mjs`.

Other jobs still belong elsewhere. File JSON, node JSON, or image exports alone: the Figma REST API. A durable two-way workflow for end users: a Figma plugin with a UI bridge.

Scene data comes from the `figma` Plugin API, never from the Figma DOM. Figma is a canvas app, so the DOM is not layer data.

## Checker and tests

Lint a plan before it runs. `--kind` is `envelope`, `code`, `result`, `image`, `audit`, and, for CDP only, `launch` and `eval`. `--list` prints every rule.

```sh
python3 skills/figma-rails/check.py path/to/command.json --kind envelope
python3 skills/figma-rails/connector.py render skills/figma-rails/snippets/doctor.js \
  --command path/to/command.json -o runs/demo/doctor.js
```

`connector.py render` inlines the envelope as `const command` and will not write code that fails `--kind code` (raw `figma.create*`, unsupported APIs, browser globals, a missing dry-run gate, unprefixed created nodes, reading `.node` before `$fig.done()`, delete by name).

Run the tests from the repository root:

```sh
python3 -m unittest discover -v
```

## Safety posture

- Every command is an envelope with an explicit `fileKey`, `pageId`, and `nodeId`. `dryRun` defaults to true. A live write needs `allowMutation`, an explicit id or parent scope, and caps on created, updated, and deleted nodes.
- Unplanned changes fail. The same scope is read back after the command. The before/after diff decides success, and the script's own report is checked against that diff.
- Deletes take explicit ids only, limited to nodes carrying a generated-name prefix or ids the command names. Nothing is deleted by name.
- CDP is a fallback. Its debugging port listens on loopback only, in a profile used for nothing else. Never tunnel it, never read cookies or tokens, and stop only the browser process you started.

## Other uses

The same rails work for matching a build to a Figma frame, and for rebuilding a webpage as editable Figma layers. Lint the plan, then judge the file by a before/after readback instead of the code's own report.

## Status

Readback records every readable property of every node, not a chosen list. Geometry, text, typography with mixed ranges, fills, strokes, text style, effects, corner radius, and auto-layout map to named field groups. Everything else, such as opacity, blend mode, rotation, constraints, component properties, and the main component of an instance, is compared as `other:<property>` and fails until the plan lists it. A property Figma adds later is compared the same way. An instance swap planned as `other:mainComponentId` covers the sublayers Figma rebuilds under new ids, and hidden layers inside instances are read too. Derived or unstable values, such as the absolute transform, render bounds, and outline geometry, are on an ignore list in `snippets/snapshot.js`, each with a reason. Values over 2,000 characters are compared by digest, and long text by a digest of the full string. Each page call re-reads the whole scope: about 2.4 s for 500 nodes and 6.4 s for 1,500 inside Figma, plus the call itself.

It does not compare image pixels behind an unchanged image hash, variable values (only which variable is bound), properties that throw when read, or more than 50 text segments per node. Check those with a screenshot.

The skill, lint checker, and runner are in this repository at `skills/figma-rails/`. This repository is MIT licensed.
