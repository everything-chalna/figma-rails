async ({ frameId, mode = "frame", includeHidden = false }) => {
  const root = await figma.getNodeByIdAsync(frameId);
  if (!root || !("children" in root)) throw new Error("frameId must resolve to a frame-like node with children");
  const imageFill = (n) => Array.isArray(n.fills) && n.fills.some((p) => p.type === "IMAGE" && p.visible !== false);
  const bounds = (n) => n.absoluteBoundingBox || { x: n.x, y: n.y, width: n.width, height: n.height };
  const rb = bounds(root);
  const nodes = [];
  const walk = (node, depth, parentId) => {
    if (!includeHidden && node.visible === false) return;
    const b = bounds(node);
    nodes.push({
      id: node.id, name: node.name, type: node.type, depth, parentId,
      visible: node.visible !== false, locked: Boolean(node.locked),
      x: b.x, y: b.y, width: b.width, height: b.height,
      imageFill: imageFill(node),
      ...(node.type === "TEXT" ? { characters: String(node.characters).slice(0, 120) } : {}),
    });
    if ("children" in node) for (const c of node.children) walk(c, depth + 1, node.id);
  };
  for (const c of root.children) walk(c, 1, root.id);
  return JSON.stringify({
    schemaVersion: "figma-sink-audit/v1", mode,
    root: { id: root.id, name: root.name, type: root.type, locked: Boolean(root.locked), x: rb.x, y: rb.y, width: rb.width, height: rb.height },
    nodes,
  });
}
