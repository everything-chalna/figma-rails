const command = {"dryRun": true, "guards": {"allowMutation": false}};
const bytes = await (await figma.getNodeByIdAsync("1:2")).exportAsync();
return { path: "/home/me/out.png", size: bytes.length };
