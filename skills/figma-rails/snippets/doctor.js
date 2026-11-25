return {
  fileName: figma.root.name,
  editorType: figma.editorType,
  currentPage: { id: figma.currentPage.id, name: figma.currentPage.name },
  pages: figma.root.children.map((p) => ({ id: p.id, name: p.name })),
  topLevelCount: figma.currentPage.children.length,
  typeofFig: typeof $fig,
};
