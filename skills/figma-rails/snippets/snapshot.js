async ({ rootId, page = 0, pageBytes = 40000, maxNodes = 2000, maxSegments = 50, maxValueChars = 2000 }) => {
  const root = await figma.getNodeByIdAsync(rootId);
  if (!root || !("children" in root)) throw new Error("rootId must resolve to a page or frame-like node: " + rootId);
  if (root.type === "PAGE") await figma.setCurrentPageAsync(root);
  const MIXED = "__mixed__";
  const READ_GROUPS = ["name", "structure", "geometry", "visibility", "characters", "font", "fill", "stroke", "textStyle", "effect", "radius", "layout", "other"];
  const IGNORED = {
    id: "the diff key itself",
    parent: "recorded as parentId",
    children: "each child records its own parentId and index",
    removed: "a removed node is absent from the walk",
    node: "self reference",
    x: "relative copy of relativeTransform; x and y hold the absolute box",
    y: "relative copy of relativeTransform; x and y hold the absolute box",
    width: "width and height hold the absolute box; a resize changes it too",
    height: "width and height hold the absolute box; a resize changes it too",
    absoluteBoundingBox: "recorded as x, y, width, height",
    absoluteTransform: "relativeTransform composed with every ancestor; the absolute box is compared",
    absoluteRenderBounds: "rasterized glyph and effect bounds, derived from the compared fields",
    fillGeometry: "outline derived from geometry, text and vector data; 180 KB for one text node",
    strokeGeometry: "outline derived from strokes and geometry",
    backgrounds: "deprecated alias of fills",
    backgroundStyleId: "deprecated alias of fillStyleId",
    fontWeight: "derived from fontName",
    padding: "alias of the four paddings",
    horizontalPadding: "alias of paddingLeft and paddingRight",
    verticalPadding: "alias of paddingTop and paddingBottom",
    inferredAutoLayout: "computed suggestion from child positions, not stored",
    inferredVariables: "computed variable suggestions, not stored",
    availableInferredVariables: "computed variable suggestions, not stored",
    variableConsumptionMap: "derived from boundVariables",
    resolvedVariableModes: "derived from explicitVariableModes and ancestors",
    isAsset: "heuristic computed from content",
    hasMissingFont: "depends on the fonts of the machine that runs the plugin",
    canUpgradeToNativeBidiSupport: "editor capability, not document content",
    expanded: "layers panel UI state",
    attachedConnectors: "state of the connector nodes, not this node",
    stuckNodes: "state of the stuck nodes, not this node",
    instances: "other nodes; slow to read on a busy component",
    mainComponent: "throws in dynamic-page files; read with getMainComponentAsync as mainComponentId",
    masterComponent: "deprecated alias of mainComponent, recorded as mainComponentId",
  };
  const SEGMENT_FIELDS = ["fontName", "fontSize", "fontStyle", "letterSpacing", "lineHeight", "listOptions", "listSpacing", "indentation", "paragraphIndent", "paragraphSpacing", "textCase", "textDecoration", "textDecorationStyle", "textDecorationOffset", "textDecorationThickness", "textDecorationColor", "textDecorationSkipInk", "textStyleId", "fills", "fillStyleId", "hyperlink", "openTypeFeatures", "boundVariables", "textWrapStyle"];
  const digest = (s) => { let h = 0x811c9dc5; for (let i = 0; i < s.length; i++) h = Math.imul(h ^ s.charCodeAt(i), 0x01000193) >>> 0; return h.toString(16).padStart(8, "0"); };
  const isNode = (v) => v && typeof v === "object" && typeof v.id === "string" && typeof v.type === "string" && "removed" in v;
  const value = (v, depth = 0) => {
    if (v === figma.mixed) return MIXED;
    if (typeof v === "number") return Math.round(v * 10000) / 10000;
    if (v === null || typeof v !== "object") return v;
    if (isNode(v)) return { nodeId: v.id };
    if (depth > 8) return "__deep__";
    if (Array.isArray(v)) return v.map((x) => value(x, depth + 1));
    return Object.fromEntries(Object.keys(v).filter((k) => typeof v[k] !== "function").map((k) => [k, value(v[k], depth + 1)]));
  };
  const bounded = (v) => {
    const text = JSON.stringify(v);
    return text !== undefined && text.length > maxValueChars ? { __digest__: digest(text), length: text.length } : v;
  };
  const propNames = (node) => {
    const names = new Set();
    for (let o = node; o && o !== Object.prototype; o = Object.getPrototypeOf(o)) for (const k of Object.getOwnPropertyNames(o)) if (k !== "constructor") names.add(k);
    return [...names].filter((k) => !(k in IGNORED)).sort();
  };
  const nodes = [];
  const walk = (node, depth, index) => {
    if (nodes.length >= maxNodes) throw new Error("snapshot over maxNodes=" + maxNodes + "; narrow rootId");
    const b = node.absoluteBoundingBox || { x: node.x, y: node.y, width: node.width, height: node.height };
    const entry = {};
    for (const k of propNames(node)) {
      let v;
      try { v = node[k]; } catch (e) { continue; }
      if (typeof v === "function" || v === undefined) continue;
      Object.assign(entry, { [k]: bounded(value(v)) });
    }
    Object.assign(entry, { id: node.id, parentId: node.parent && node.parent.id, depth, index, x: value(b.x), y: value(b.y), width: value(b.width), height: value(b.height) });
    if (node.type === "TEXT") {
      const text = String(node.characters);
      Object.assign(entry, { characters: text.slice(0, 200), ...(text.length > 200 ? { charactersDigest: digest(text), charactersLength: text.length } : {}) });
      const mixed = SEGMENT_FIELDS.filter((k) => k in node && node[k] === figma.mixed);
      if (mixed.length) {
        const segs = node.getStyledTextSegments(mixed);
        Object.assign(entry, {
          segments: segs.slice(0, maxSegments).map((seg) => Object.fromEntries([["start", seg.start], ["end", seg.end], ...mixed.map((k) => [k, bounded(value(seg[k]))])])),
          ...(segs.length > maxSegments ? { segmentsTruncated: segs.length } : {}),
        });
      }
    }
    nodes.push(entry);
    if (node.type === "INSTANCE") instances.push([entry, node]);
    if ("children" in node) node.children.forEach((c, i) => walk(c, depth + 1, i));
  };
  const instances = [];
  // use_figma runs with skipInvisibleInstanceChildren on, which drops hidden layers inside instances
  // from `children`: hiding one would read as a delete, and a hidden one would never be compared.
  const skipInvisible = figma.skipInvisibleInstanceChildren;
  figma.skipInvisibleInstanceChildren = false;
  try {
    root.children.forEach((c, i) => walk(c, 1, i));
  } finally {
    figma.skipInvisibleInstanceChildren = skipInvisible;
  }
  for (const [entry, node] of instances) {
    const main = await node.getMainComponentAsync();
    Object.assign(entry, { mainComponentId: main ? main.id : null });
  }
  // use_figma refuses a return value over 48,000 bytes, and one node is about 2.4 KB. Values shared by
  // most nodes of a type go once into `defaults`, and the nodes are split into pages of `pageBytes`.
  const defaults = {};
  const byType = {};
  for (const n of nodes) (byType[n.type] = byType[n.type] || []).push(n);
  for (const [type, list] of Object.entries(byType)) {
    if (list.length < 2) continue;
    const counts = {};
    for (const n of list) for (const k of Object.keys(n)) if (k !== "id" && k !== "type") { const key = k + "\u0000" + JSON.stringify(n[k]); counts[key] = (counts[key] || 0) + 1; }
    const best = {};
    for (const [key, c] of Object.entries(counts)) { const cut = key.indexOf("\u0000"); const k = key.slice(0, cut); if (c >= 2 && (!best[k] || c > best[k][1])) best[k] = [key.slice(cut + 1), c]; }
    defaults[type] = Object.fromEntries(Object.entries(best).map(([k, [v]]) => [k, JSON.parse(v)]));
  }
  const packed = nodes.map((n) => {
    const d = defaults[n.type] || {};
    const out = {};
    for (const k of Object.keys(n)) if (!(k in d) || JSON.stringify(d[k]) !== JSON.stringify(n[k])) out[k] = n[k];
    const absent = Object.keys(d).filter((k) => !(k in n));
    return absent.length ? Object.assign(out, { __absent__: absent }) : out;
  });
  const snapshotDigest = digest(JSON.stringify([defaults, packed]));
  const pages = [[]];
  let used = JSON.stringify(defaults).length;
  for (const n of packed) {
    const size = JSON.stringify(n).length + 1;
    if (pages[pages.length - 1].length && used + size > pageBytes) { pages.push([]); used = 0; }
    pages[pages.length - 1].push(n);
    used += size;
  }
  if (!(page >= 0 && page < pages.length)) throw new Error("page " + page + " is outside 0.." + (pages.length - 1));
  return {
    schemaVersion: "figma-sink-snapshot/v3", transport: "figma-sink-pages/v1", readGroups: READ_GROUPS, ignored: Object.keys(IGNORED).sort(), rootId: root.id, rootType: root.type,
    snapshotDigest, page, pages: pages.length, total: packed.length, ...(page === 0 ? { defaults } : {}), nodes: pages[page],
  };
}
