const command = {"dryRun": true, "guards": {"allowMutation": false}};
const args = {};
(() => {
  const f = typeof figma !== "undefined" ? figma : globalThis.figma;
  return {
    href: location.href,
    title: document.title,
    typeofFigma: typeof figma,
    apiVersion: f && f.apiVersion,
    editorType: f && f.editorType,
    fileName: f && f.root && f.root.name,
    currentPage: f && f.currentPage && { id: f.currentPage.id, name: f.currentPage.name },
    selection: f && f.currentPage ? f.currentPage.selection.map((n) => ({ id: n.id, name: n.name, type: n.type })) : [],
    canFind: !!(f && f.currentPage && typeof f.currentPage.findAll === "function"),
  };
})()
