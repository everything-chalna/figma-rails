const command = {"dryRun": false, "guards": {"allowMutation": true, "generatedPrefix": "__figma_sink__"}};
if (command.dryRun !== false || command.guards.allowMutation !== true) return { plan: [] };
const card = $fig.autoLayout({ name: `${command.guards.generatedPrefix} card` });
return { id: card.node.id };
