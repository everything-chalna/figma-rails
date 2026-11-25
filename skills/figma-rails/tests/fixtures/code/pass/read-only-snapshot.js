const command = {"dryRun": true, "guards": {"allowMutation": false}};
const args = {"rootId": "0:1"};
return await (async ({ rootId, maxNodes = 2000 }) => {
  const root = await figma.getNodeByIdAsync(rootId);
  if (!root || !("children" in root)) throw new Error("rootId must resolve to a page or frame-like node: " + rootId);
  if (root.type === "PAGE") await figma.setCurrentPageAsync(root);
  const nodes = [];
  const walk = (node, depth) => {
    if (nodes.length >= maxNodes) throw new Error("snapshot over maxNodes=" + maxNodes + "; narrow rootId");
    const b = node.absoluteBoundingBox || { x: node.x, y: node.y, width: node.width, height: node.height };
    nodes.push({
      id: node.id, name: node.name, type: node.type, parentId: node.parent && node.parent.id, depth,
      x: b.x, y: b.y, width: b.width, height: b.height,
      visible: node.visible !== false, locked: Boolean(node.locked),
      ...(node.type === "TEXT" ? { characters: String(node.characters).slice(0, 200) } : {}),
    });
    if ("children" in node) for (const c of node.children) walk(c, depth + 1);
  };
  for (const c of root.children) walk(c, 1);
  return { schemaVersion: "figma-sink-snapshot/v1", rootId: root.id, rootType: root.type, nodes };
})(args);
