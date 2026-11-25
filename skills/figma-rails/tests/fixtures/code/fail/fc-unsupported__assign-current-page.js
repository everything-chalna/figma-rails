const command = {"dryRun": true, "guards": {"allowMutation": false}};
figma.currentPage = figma.root.children[1];
return figma.currentPage.id;
