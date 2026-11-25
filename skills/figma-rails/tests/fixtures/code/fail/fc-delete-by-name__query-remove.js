const command = {"dryRun": false, "guards": {"allowMutation": true, "generatedPrefix": "__figma_sink__"}};
if (command.dryRun !== false || command.guards.allowMutation !== true) return { plan: [] };
$fig.query('FRAME[name="Card"]').remove();
return { ok: true };
