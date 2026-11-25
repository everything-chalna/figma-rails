async ({ runId, assetId, nodeId, format = "PNG", scale = 1, chunkSize = 524288 }) => {
  const node = await figma.getNodeByIdAsync(nodeId);
  if (!node) throw new Error("node not found: " + nodeId);
  const opts = format === "SVG_STRING" ? { format } : { format, constraint: { type: "SCALE", value: scale } };
  const out = await node.exportAsync(opts);
  let base64;
  if (typeof out === "string") {
    base64 = btoa(unescape(encodeURIComponent(out)));
  } else {
    let binary = "";
    for (let i = 0; i < out.length; i += 0x8000) binary += String.fromCharCode.apply(null, out.subarray(i, i + 0x8000));
    base64 = btoa(binary);
  }
  const chunks = [];
  for (let i = 0; i < base64.length; i += chunkSize) chunks.push(base64.slice(i, i + chunkSize));
  globalThis.__figmaSinkBridge = globalThis.__figmaSinkBridge || {};
  globalThis.__figmaSinkBridge[runId] = globalThis.__figmaSinkBridge[runId] || {};
  globalThis.__figmaSinkBridge[runId][assetId] = chunks;
  return { runId, assetId, nodeId, format, scale, encoding: "base64", totalChunks: chunks.length };
}
