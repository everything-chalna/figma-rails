const command = {"schemaVersion": "figma-cli-command/v1", "transport": "figma-mcp-use-figma", "command": "create-test-card", "target": {"fileKey": "AbCdEfGhIjKlMnOpQrStUv", "pageId": "0:1", "nodeId": "0:1", "rootNodeId": "0:1"}, "skillNames": "figma-use,figma-generate-design", "sessionId": "00000000-0000-4000-8000-000000000000", "args": {}, "expectedChanges": {"mutatedNodeIds": [], "createdNames": ["__figma_sink__ connector test card", "Title", "Body"]}, "timeoutMs": 30000, "guards": {"allowMutation": true, "allowedNodeIds": [], "allowedParentIds": ["0:1"], "generatedPrefix": "__figma_sink__", "requireGeneratedPrefixForDelete": true, "requireExplicitIdsForDelete": true, "maxCreatedNodes": 3, "maxDeletedNodes": 0, "maxUpdatedNodes": 0}, "steps": ["snapshot-before", "revalidate-target", "apply", "snapshot-after", "compare-plan"], "commandId": "cmd_create_live", "script": "runs/cmd_create_live/create.js", "dryRun": false};
const args = {};
const plan = { create: command.expectedChanges.createdNames, parentId: command.target.pageId };
if (command.dryRun !== false || command.guards.allowMutation !== true) return { dryRun: true, plan };
const page = await figma.getNodeByIdAsync(command.target.pageId);
await figma.setCurrentPageAsync(page);
const card = $fig.autoLayout(
  { name: `${command.guards.generatedPrefix} connector test card`, layoutMode: "VERTICAL", width: 360, paddingTop: 24, paddingBottom: 24, paddingLeft: 24, paddingRight: 24, itemSpacing: 8, cornerRadius: 12, fills: [figma.util.solidPaint("#F4F1EA")] },
  [
    $fig.text({ name: "Title", characters: "figma-sink connector test", fontName: { family: "Inter", style: "Bold" }, fontSize: 20, fills: [figma.util.solidPaint("#1A1A1A")] }),
    $fig.text({ name: "Body", characters: "Linted before use_figma, verified by a before/after snapshot diff.", fontName: { family: "Inter", style: "Regular" }, fontSize: 14, layoutSizingHorizontal: "FILL", textAutoResize: "HEIGHT", fills: [figma.util.solidPaint("#4A4A4A")] }),
  ],
);
await $fig.done();
return { createdNodeIds: [card.node.id, ...card.node.children.map((c) => c.id)] };
