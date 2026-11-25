const command = {"schemaVersion": "figma-cli-command/v1", "transport": "figma-mcp-use-figma", "command": "edit-body-text", "target": {"fileKey": "AbCdEfGhIjKlMnOpQrStUv", "pageId": "0:1", "nodeId": "2:4", "rootNodeId": "0:1"}, "skillNames": "figma-use,figma-generate-design", "sessionId": "00000000-0000-4000-8000-000000000000", "args": {}, "expectedChanges": {"mutatedNodeIds": ["2:4"], "createdNames": []}, "timeoutMs": 30000, "guards": {"allowMutation": true, "allowedNodeIds": ["2:4"], "allowedParentIds": [], "generatedPrefix": "__figma_sink__", "requireGeneratedPrefixForDelete": true, "requireExplicitIdsForDelete": true, "maxCreatedNodes": 0, "maxDeletedNodes": 0, "maxUpdatedNodes": 1}, "steps": ["snapshot-before", "revalidate-target", "apply", "snapshot-after", "compare-plan"], "commandId": "cmd_edit_body", "script": "runs/cmd_edit_body/edit.js", "dryRun": false};
const args = {};
const plan = { update: command.expectedChanges.mutatedNodeIds };
if (command.dryRun !== false || command.guards.allowMutation !== true) return { dryRun: true, plan };
$fig.get(command.target.nodeId).set({ characters: "Linted before use_figma, verified by a before/after snapshot diff. This longer body wraps onto more lines, so the hugging parent card grows too." });
await $fig.done();
return { updatedNodeIds: [command.target.nodeId] };
