const command = {"dryRun": true, "guards": {"allowMutation": false}};
return figma.currentPage.findAll((n) => n.name === "Card").map((n) => n.id);
