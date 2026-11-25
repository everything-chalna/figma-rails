const command = {"dryRun": false, "guards": {"allowMutation": true, "generatedPrefix": "__figma_sink__"}};
if (command.dryRun !== false || command.guards.allowMutation !== true) return { plan: [] };
(async () => {
  const node = await figma.getNodeByIdAsync("1:2");
  return node.name;
})();
