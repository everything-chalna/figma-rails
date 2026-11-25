async ({ rootId, maxNodes = 2000, maxSegments = 50 }) => {
  const root = await figma.getNodeByIdAsync(rootId);
  if (!root || !("children" in root)) throw new Error("rootId must resolve to a page or frame-like node: " + rootId);
  if (root.type === "PAGE") await figma.setCurrentPageAsync(root);
  const MIXED = "__mixed__";
  const READ_GROUPS = ["name", "structure", "geometry", "visibility", "characters", "font", "fill", "stroke", "textStyle", "effect", "radius", "layout"];
  const num = (v) => (typeof v === "number" ? Math.round(v * 10000) / 10000 : v);
  const unit = (v) => (v === figma.mixed ? MIXED : v && typeof v === "object" ? { unit: v.unit, ...("value" in v ? { value: num(v.value) } : {}) } : v);
  const rgba = (c) => c && { r: num(c.r), g: num(c.g), b: num(c.b), ...("a" in c ? { a: num(c.a) } : {}) };
  const paint = (p) => {
    const out = { type: p.type, visible: p.visible !== false, opacity: num(p.opacity === undefined ? 1 : p.opacity) };
    if (p.blendMode && p.blendMode !== "NORMAL") out.blendMode = p.blendMode;
    if (p.type === "SOLID") out.color = rgba(p.color);
    if (p.gradientStops) out.gradientStops = p.gradientStops.map((s) => ({ position: num(s.position), color: rgba(s.color) }));
    if (p.gradientTransform) out.gradientTransform = p.gradientTransform.map((row) => row.map(num));
    if (p.type === "IMAGE" || p.type === "VIDEO") { out.scaleMode = p.scaleMode; out.mediaHash = p.imageHash || p.videoHash || null; }
    if (p.boundVariables && p.boundVariables.color) out.colorVariableId = p.boundVariables.color.id;
    return out;
  };
  const paints = (v) => (v === figma.mixed ? MIXED : Array.isArray(v) ? v.map(paint) : v);
  const style = (v) => (v === figma.mixed ? MIXED : v || null);
  const value = (v) => (typeof v === "number" ? num(v) : Array.isArray(v) ? v.map(value) : v && typeof v === "object" ? Object.fromEntries(Object.entries(v).map(([k, x]) => [k, value(x)])) : v);
  const effect = (e) => {
    const out = { visible: e.visible !== false };
    for (const [k, v] of Object.entries(e)) {
      if (k === "visible") continue;
      if (k === "boundVariables") { if (v && Object.keys(v).length) out.boundVariables = Object.fromEntries(Object.entries(v).map(([p, a]) => [p, a && a.id])); continue; }
      out[k] = value(v);
    }
    return out;
  };
  const pick = (node, keys) => Object.fromEntries(keys.filter((k) => k in node).map((k) => [k, node[k] === figma.mixed ? MIXED : num(node[k])]));
  const FRAME_LAYOUT = ["layoutMode", "primaryAxisSizingMode", "counterAxisSizingMode", "paddingLeft", "paddingRight", "paddingTop", "paddingBottom", "itemSpacing", "counterAxisSpacing", "primaryAxisAlignItems", "counterAxisAlignItems", "layoutWrap"];
  const CHILD_LAYOUT = ["layoutAlign", "layoutGrow", "layoutPositioning", "layoutSizingHorizontal", "layoutSizingVertical"];
  const CORNERS = ["topLeftRadius", "topRightRadius", "bottomRightRadius", "bottomLeftRadius"];
  const TEXT_FIELDS = ["fontName", "fontSize", "lineHeight", "letterSpacing", "textStyleId", "fills", "fillStyleId"];
  const segValue = (k, v) => (k === "fills" ? paints(v) : k === "lineHeight" || k === "letterSpacing" ? unit(v) : k === "fontName" ? { family: v.family, style: v.style } : num(v));
  const nodes = [];
  const walk = (node, depth) => {
    if (nodes.length >= maxNodes) throw new Error("snapshot over maxNodes=" + maxNodes + "; narrow rootId");
    const b = node.absoluteBoundingBox || { x: node.x, y: node.y, width: node.width, height: node.height };
    const entry = {
      id: node.id, name: node.name, type: node.type, parentId: node.parent && node.parent.id, depth,
      x: b.x, y: b.y, width: b.width, height: b.height,
      visible: node.visible !== false, locked: Boolean(node.locked),
    };
    if ("fills" in node) Object.assign(entry, { fills: paints(node.fills), fillStyleId: style(node.fillStyleId) });
    if ("strokes" in node) Object.assign(entry, { strokes: paints(node.strokes), strokeStyleId: style(node.strokeStyleId), strokeWeight: node.strokeWeight === figma.mixed ? MIXED : num(node.strokeWeight) });
    if ("effects" in node) Object.assign(entry, { effects: node.effects.map(effect), effectStyleId: style(node.effectStyleId) });
    if ("cornerRadius" in node) Object.assign(entry, { cornerRadius: node.cornerRadius === figma.mixed ? MIXED : num(node.cornerRadius), ...(node.cornerRadius === figma.mixed ? pick(node, CORNERS) : {}), ...pick(node, ["cornerSmoothing"]) });
    if ("layoutMode" in node) Object.assign(entry, node.layoutMode === "NONE" ? { layoutMode: "NONE" } : pick(node, FRAME_LAYOUT));
    if (node.parent && "layoutMode" in node.parent && node.parent.layoutMode !== "NONE") Object.assign(entry, pick(node, CHILD_LAYOUT));
    if (node.type === "TEXT") {
      Object.assign(entry, {
        characters: String(node.characters).slice(0, 200),
        fontName: node.fontName === figma.mixed ? MIXED : { family: node.fontName.family, style: node.fontName.style },
        fontSize: node.fontSize === figma.mixed ? MIXED : num(node.fontSize),
        lineHeight: unit(node.lineHeight),
        letterSpacing: unit(node.letterSpacing),
        textAlignHorizontal: node.textAlignHorizontal,
        textAlignVertical: node.textAlignVertical,
        textStyleId: style(node.textStyleId),
      });
      const mixed = TEXT_FIELDS.filter((k) => node[k] === figma.mixed);
      if (mixed.length) {
        const segs = node.getStyledTextSegments(mixed);
        Object.assign(entry, {
          segments: segs.slice(0, maxSegments).map((seg) => Object.fromEntries([["start", seg.start], ["end", seg.end], ...mixed.map((k) => [k, segValue(k, seg[k])])])),
          ...(segs.length > maxSegments ? { segmentsTruncated: segs.length } : {}),
        });
      }
    }
    nodes.push(entry);
    if ("children" in node) for (const c of node.children) walk(c, depth + 1);
  };
  for (const c of root.children) walk(c, 1);
  return { schemaVersion: "figma-sink-snapshot/v2", readGroups: READ_GROUPS, rootId: root.id, rootType: root.type, nodes };
}
