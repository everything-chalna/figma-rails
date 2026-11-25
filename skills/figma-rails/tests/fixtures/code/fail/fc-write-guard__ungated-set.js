const command = {"dryRun": true, "guards": {"allowMutation": false}};
$fig.get("2:4").set({ characters: "New body" });
return { updatedNodeIds: ["2:4"] };
