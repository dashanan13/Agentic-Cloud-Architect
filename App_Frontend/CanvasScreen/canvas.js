// ===== CanvasScreen Canvas Implementation =====
// This is a comprehensive implementation porting all canvas functionality 
// from the main canvas.js to work with CanvasScreen layout

// ===== UI Element References =====
const canvasViewportEl = document.getElementById("canvas-viewport");
const canvasGridEl = document.getElementById("canvas-grid");
const canvasLayerEl = document.getElementById("canvas-layer");
const canvasEdgesEl = document.getElementById("canvas-edges");
const canvasZoomOutBtn = document.getElementById("canvas-zoom-out");
const canvasZoomInBtn = document.getElementById("canvas-zoom-in");
const canvasResetViewBtn = document.getElementById("canvas-reset-view");
const canvasZoomLabelEl = document.getElementById("canvas-zoom-label");
const resourceListEl = document.getElementById("resource-list");
const searchInput = document.getElementById("search-input");
const propertyContentEl = document.getElementById("property-content");
const projectNameDisplay = document.getElementById("project-name-suffix-display");
const projectIacIconEl = document.getElementById("project-iac-icon");
const btnProjectSave = document.getElementById("btn-project-save");
const btnValidate = document.getElementById("btn-validate");
const btnExportDiagram = document.getElementById("btn-export-diagram");
const btnProjectSettings = document.getElementById("btn-project-settings-template");
const btnBackProjects = document.getElementById("btn-back-projects");
const chatRuntimeModelEl = document.getElementById("chat-runtime-model");
const chatRuntimeMcpEl = document.getElementById("chat-runtime-mcp");
const centerSystemMessageEl = document.getElementById("center-system-message");
const tabsElements = document.querySelectorAll('.tab[role="tab"]');
const tabPanelsElements = document.querySelectorAll('.tab-pane[role="tabpanel"]');

// ===== Constants =====
const CANVAS_ZOOM = {
  min: 0.2,
  max: 4,
  step: 0.05,
  grid: 40
};

const CANVAS_WORLD = {
  width: 6000,
  height: 4000,
  defaultOffsetX: -900,  // Center-focused positioning at 50% zoom (canvas middle at 3000,2000)
  defaultOffsetY: -600   // Center-focused positioning at 50% zoom
};

const CANVAS_CONTAINER = {
  minWidth: 220,
  minHeight: 140,
  defaultWidth: 360,
  defaultHeight: 240,
  headerHeight: 32,
  padding: 12
};

const MAX_PROJECT_NAME_LENGTH = 50;
const AUTOSAVE_INTERVAL_MS = 30000;

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function createDefaultCanvasView() {
  // Start near center with a comfortable zoom while allowing infinite pan.
  return {
    x: CANVAS_WORLD.defaultOffsetX,
    y: CANVAS_WORLD.defaultOffsetY,
    zoom: 0.5
  };
}

// ===== State Management =====
const state = {
  currentProject: null,
  selectedResource: null,
  canvasView: createDefaultCanvasView(),
  canvasItems: [],
  canvasConnections: [],
  selectedConnectionId: null,
  edgeDraft: {
    active: false,
    sourceId: null,
    sourceAnchor: "right",
    targetId: null,
    targetAnchor: "left",
    endClientX: 0,
    endClientY: 0
  },
  searchTerm: ""
};

const canvasInteraction = {
  isPanning: false,
  spacePanMode: false,
  panOriginX: 0,
  panOriginY: 0,
  viewOriginX: 0,
  viewOriginY: 0,
  draggingItemId: null,
  dragOriginX: 0,
  dragOriginY: 0,
  itemOriginX: 0,
  itemOriginY: 0,
  resizingItemId: null,
  resizeOriginX: 0,
  resizeOriginY: 0,
  widthOrigin: 0,
  heightOrigin: 0
};

// ===== Helper Functions =====

function getItemById(itemId) {
  return state.canvasItems.find((candidate) => candidate.id === itemId) || null;
}

function getChildrenByParentId(parentId) {
  return state.canvasItems.filter((candidate) => candidate.parentId === parentId);
}

function getItemWorldPosition(itemId) {
  const item = getItemById(itemId);
  if (!item) {
    return { x: 0, y: 0 };
  }

  if (!item.parentId) {
    return { x: item.x, y: item.y };
  }

  const parentWorld = getItemWorldPosition(item.parentId);
  return {
    x: parentWorld.x + CANVAS_CONTAINER.padding + item.x,
    y: parentWorld.y + CANVAS_CONTAINER.headerHeight + CANVAS_CONTAINER.padding + item.y
  };
}

function toWorldPoint(clientX, clientY) {
  const rect = canvasViewportEl.getBoundingClientRect();
  const screenX = clientX - rect.left;
  const screenY = clientY - rect.top;

  return {
    x: (screenX - state.canvasView.x) / state.canvasView.zoom,
    y: (screenY - state.canvasView.y) / state.canvasView.zoom
  };
}

function toWorldFromScreenPoint(screenX, screenY) {
  return {
    x: (screenX - state.canvasView.x) / state.canvasView.zoom,
    y: (screenY - state.canvasView.y) / state.canvasView.zoom
  };
}

function isDescendant(candidateId, ancestorId) {
  let cursor = getItemById(candidateId);
  while (cursor?.parentId) {
    if (cursor.parentId === ancestorId) {
      return true;
    }
    cursor = getItemById(cursor.parentId);
  }
  return false;
}

function getContainerInnerWorldBounds(container) {
  const world = getItemWorldPosition(container.id);
  const width = clamp(Number(container.width) || CANVAS_CONTAINER.defaultWidth, CANVAS_CONTAINER.minWidth, 2400);
  const height = clamp(Number(container.height) || CANVAS_CONTAINER.defaultHeight, CANVAS_CONTAINER.minHeight, 2400);
  const left = world.x + CANVAS_CONTAINER.padding;
  const top = world.y + CANVAS_CONTAINER.headerHeight + CANVAS_CONTAINER.padding;
  const right = world.x + width - CANVAS_CONTAINER.padding;
  const bottom = world.y + height - CANVAS_CONTAINER.padding;

  return { left, top, right, bottom };
}

function getContainerAtWorldPoint(worldX, worldY, ignoreItemId = null) {
  const containers = state.canvasItems
    .filter((item) => item.isContainer && item.id !== ignoreItemId && !isDescendant(item.id, ignoreItemId))
    .map((item) => ({ item, bounds: getContainerInnerWorldBounds(item) }))
    .filter(({ bounds }) => worldX >= bounds.left && worldX <= bounds.right && worldY >= bounds.top && worldY <= bounds.bottom)
    .sort((first, second) => {
      const firstArea = (first.bounds.right - first.bounds.left) * (first.bounds.bottom - first.bounds.top);
      const secondArea = (second.bounds.right - second.bounds.left) * (second.bounds.bottom - second.bounds.top);
      return firstArea - secondArea;
    });

  return containers.length ? containers[0].item : null;
}

function moveItemToParent(item, nextParentId) {
  const world = getItemWorldPosition(item.id);
  item.parentId = nextParentId;

  if (!nextParentId) {
    item.x = world.x;
    item.y = world.y;
    return;
  }

  const parentWorld = getItemWorldPosition(nextParentId);
  item.x = world.x - parentWorld.x - CANVAS_CONTAINER.padding;
  item.y = world.y - parentWorld.y - CANVAS_CONTAINER.headerHeight - CANVAS_CONTAINER.padding;
}

function getVisualLayer(item) {
  if (item.isContainer) {
    return 0;
  }
  return 2;
}

function isContainerResource(resource) {
  const typeName = String(resource.resourceType || resource.name || "").toLowerCase();
  return typeName.includes("group") || typeName.includes("network") || typeName.includes("container");
}

function isConnectableItem(item) {
  if (!item) return false;
  const typeName = String(item.resourceType || "").toLowerCase();
  return typeName.includes("network") || typeName.includes("subnet") || typeName.includes("gateway");
}

function getNextResourceDefaultName() {
  const existingNames = state.canvasItems.map((item) => item.name);
  let counter = 1;
  let name = `Resource-${counter}`;
  while (existingNames.includes(name)) {
    counter++;
    name = `Resource-${counter}`;
  }
  return name;
}

function persistCanvasLocal() {
  if (!state.currentProject) {
    return;
  }

  state.currentProject.canvasView = {
    x: state.canvasView.x,
    y: state.canvasView.y,
    zoom: state.canvasView.zoom
  };
  state.currentProject.canvasItems = state.canvasItems.map((item) => ({ ...item }));
  state.currentProject.canvasConnections = state.canvasConnections.map((connection) => ({ ...connection }));
  saveCurrentProject();
}

function saveCurrentProject() {
  if (!state.currentProject) return;
  localStorage.setItem("currentProject", JSON.stringify(state.currentProject));
}

function flushPendingCanvasEditsForManualSave() {
  if (!state.currentProject) return;
  
  const activeElement = document.activeElement;
  if (activeElement instanceof HTMLElement) {
    const shouldBlurActiveElement = activeElement === projectNameDisplay
      || (propertyContentEl instanceof HTMLElement && propertyContentEl.contains(activeElement));
    if (shouldBlurActiveElement) {
      activeElement.blur();
    }
  }

  persistCanvasLocal();
}

// ===== Rendering Functions =====

function buildRemoveControl(itemId) {
  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "canvas-node-remove";
  removeButton.dataset.itemId = itemId;
  removeButton.setAttribute("aria-label", "Remove resource");
  removeButton.title = "Remove";
  removeButton.textContent = "×";
  return removeButton;
}

function buildConnectHandle(itemId, anchor) {
  const handle = document.createElement("button");
  handle.type = "button";
  handle.className = "canvas-connect-handle";
  handle.dataset.itemId = itemId;
  handle.dataset.anchor = anchor;
  handle.setAttribute("aria-label", `Connect from ${anchor}`);
  return handle;
}

function getNodeScreenRect(itemId) {
  if (!canvasViewportEl) {
    return null;
  }

  const nodeEl = canvasLayerEl?.querySelector(`.canvas-node[data-item-id="${itemId}"]`);
  if (!nodeEl) {
    return null;
  }

  const viewportRect = canvasViewportEl.getBoundingClientRect();
  const nodeRect = nodeEl.getBoundingClientRect();

  return {
    left: nodeRect.left - viewportRect.left,
    top: nodeRect.top - viewportRect.top,
    width: nodeRect.width,
    height: nodeRect.height
  };
}

function getAnchorScreenPoint(itemId, anchor = "right") {
  const rect = getNodeScreenRect(itemId);
  if (!rect) {
    return null;
  }

  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;

  if (anchor === "top") {
    return { x: cx, y: rect.top };
  }
  if (anchor === "bottom") {
    return { x: cx, y: rect.top + rect.height };
  }
  if (anchor === "left") {
    return { x: rect.left, y: cy };
  }
  return { x: rect.left + rect.width, y: cy };
}

function worldToScreenPoint(worldX, worldY) {
  return {
    x: worldX * state.canvasView.zoom + state.canvasView.x,
    y: worldY * state.canvasView.zoom + state.canvasView.y
  };
}

function getNodeWorldRect(itemId) {
  const item = getItemById(itemId);
  if (!item) {
    return null;
  }

  const world = getItemWorldPosition(itemId);
  if (item.isContainer) {
    const width = clamp(Number(item.width) || CANVAS_CONTAINER.defaultWidth, CANVAS_CONTAINER.minWidth, 2400);
    const height = clamp(Number(item.height) || CANVAS_CONTAINER.defaultHeight, CANVAS_CONTAINER.minHeight, 2400);
    return {
      left: world.x,
      top: world.y,
      width,
      height
    };
  }

  const nodeEl = canvasLayerEl?.querySelector(`.canvas-node[data-item-id="${itemId}"]`);
  if (nodeEl) {
    const rect = nodeEl.getBoundingClientRect();
    const width = Math.max(180, rect.width / state.canvasView.zoom);
    const height = Math.max(120, rect.height / state.canvasView.zoom);
    return {
      left: world.x,
      top: world.y,
      width,
      height
    };
  }

  return {
    left: world.x,
    top: world.y,
    width: 180,
    height: 120
  };
}

function getAnchorWorldPoint(itemId, anchor = "right") {
  const rect = getNodeWorldRect(itemId);
  if (!rect) {
    return null;
  }

  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;

  if (anchor === "top") {
    return { x: cx, y: rect.top };
  }
  if (anchor === "bottom") {
    return { x: cx, y: rect.top + rect.height };
  }
  if (anchor === "left") {
    return { x: rect.left, y: cy };
  }
  return { x: rect.left + rect.width, y: cy };
}

function getConnectionPath(connection) {
  const fromWorld = getAnchorWorldPoint(connection.fromId, connection.sourceAnchor || "right");
  const toWorld = getAnchorWorldPoint(connection.toId, connection.targetAnchor || "left");
  if (!fromWorld || !toWorld) {
    return null;
  }

  const fromScreen = worldToScreenPoint(fromWorld.x, fromWorld.y);
  const toScreen = worldToScreenPoint(toWorld.x, toWorld.y);

  return {
    path: `M ${fromScreen.x} ${fromScreen.y} L ${toScreen.x} ${toScreen.y}`,
    midX: (fromScreen.x + toScreen.x) / 2,
    midY: (fromScreen.y + toScreen.y) / 2
  };
}

function ensureEdgesDefs() {
  if (!canvasEdgesEl) {
    return;
  }

  if (canvasEdgesEl.querySelector("defs")) {
    return;
  }

  const namespace = "http://www.w3.org/2000/svg";
  const defs = document.createElementNS(namespace, "defs");

  const markerEnd = document.createElementNS(namespace, "marker");
  markerEnd.setAttribute("id", "edge-arrow-end");
  markerEnd.setAttribute("viewBox", "0 0 10 10");
  markerEnd.setAttribute("refX", "9");
  markerEnd.setAttribute("refY", "5");
  markerEnd.setAttribute("markerWidth", "4");
  markerEnd.setAttribute("markerHeight", "4");
  markerEnd.setAttribute("markerUnits", "strokeWidth");
  markerEnd.setAttribute("orient", "auto-start-reverse");

  const arrowEndPath = document.createElementNS(namespace, "path");
  arrowEndPath.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrowEndPath.setAttribute("fill", "#2563eb");
  markerEnd.appendChild(arrowEndPath);

  defs.appendChild(markerEnd);
  canvasEdgesEl.appendChild(defs);
}

function renderCanvasConnections() {
  if (!canvasEdgesEl || !canvasViewportEl) {
    return;
  }

  ensureEdgesDefs();

  const namespace = "http://www.w3.org/2000/svg";
  const viewportRect = canvasViewportEl.getBoundingClientRect();
  canvasEdgesEl.setAttribute("width", `${Math.max(0, viewportRect.width)}`);
  canvasEdgesEl.setAttribute("height", `${Math.max(0, viewportRect.height)}`);
  canvasEdgesEl.removeAttribute("viewBox");

  const defs = canvasEdgesEl.querySelector("defs");
  canvasEdgesEl.innerHTML = "";
  if (defs) {
    canvasEdgesEl.appendChild(defs);
  } else {
    ensureEdgesDefs();
  }

  state.canvasConnections.forEach((connection) => {
    const pathData = getConnectionPath(connection);
    if (!pathData) {
      return;
    }

    const edge = document.createElementNS(namespace, "path");
    edge.classList.add("canvas-edge");
    edge.dataset.connectionId = connection.id;
    edge.setAttribute("d", pathData.path);

    if (state.selectedConnectionId === connection.id) {
      edge.classList.add("is-selected");
    }

    if (connection.direction === "bi") {
      edge.setAttribute("marker-start", "url(#edge-arrow-end)");
      edge.setAttribute("marker-end", "url(#edge-arrow-end)");
    } else {
      edge.removeAttribute("marker-start");
      edge.setAttribute("marker-end", "url(#edge-arrow-end)");
    }

    canvasEdgesEl.appendChild(edge);
  });

  if (state.edgeDraft.active && state.edgeDraft.sourceId) {
    const fromWorld = getAnchorWorldPoint(state.edgeDraft.sourceId, state.edgeDraft.sourceAnchor || "right");
    if (fromWorld) {
      const fromScreen = worldToScreenPoint(fromWorld.x, fromWorld.y);
      const viewportRect = canvasViewportEl.getBoundingClientRect();
      const toScreen = {
        x: state.edgeDraft.endClientX - viewportRect.left,
        y: state.edgeDraft.endClientY - viewportRect.top
      };
      const edge = document.createElementNS(namespace, "path");
      edge.classList.add("canvas-edge", "canvas-edge--draft");
      edge.setAttribute("d", `M ${fromScreen.x} ${fromScreen.y} L ${toScreen.x} ${toScreen.y}`);
      canvasEdgesEl.appendChild(edge);
    }
  }
}

function renderCanvasItems() {
  if (!canvasLayerEl) {
    return;
  }

  canvasLayerEl.innerHTML = "";

  function renderNode(item) {
    const worldCoords = getItemWorldPosition(item.id);
    
    const nodeEl = document.createElement("div");
    nodeEl.className = "canvas-node";
    nodeEl.dataset.itemId = item.id;
    nodeEl.style.transform = `translate(${worldCoords.x}px, ${worldCoords.y}px)`;
    
    const resourceType = item.resourceType || item.name;

    const iconEl = document.createElement("img");
    iconEl.src = item.iconSrc || "/Assets/Icons/resource-default.png";
    iconEl.alt = `${resourceType} icon`;
    iconEl.draggable = false;

    if (item.isContainer) {
      const titleEl = document.createElement("span");
      titleEl.textContent = `${resourceType}: ${item.name}`;

      nodeEl.classList.add("canvas-node--container");
      nodeEl.style.width = `${item.width || CANVAS_CONTAINER.defaultWidth}px`;
      nodeEl.style.height = `${item.height || CANVAS_CONTAINER.defaultHeight}px`;

      const headerEl = document.createElement("div");
      headerEl.className = "canvas-container-header";
      headerEl.appendChild(iconEl);
      headerEl.appendChild(titleEl);
      headerEl.appendChild(buildRemoveControl(item.id));

      const bodyEl = document.createElement("div");
      bodyEl.className = "canvas-container-body";

      nodeEl.appendChild(headerEl);
      nodeEl.appendChild(bodyEl);

      const resizeHandle = document.createElement("div");
      resizeHandle.className = "canvas-resize-handle";
      resizeHandle.dataset.itemId = item.id;
      resizeHandle.title = "Resize container";
      nodeEl.appendChild(resizeHandle);

      if (isConnectableItem(item)) {
        ["top", "right", "bottom", "left"].forEach((anchor) => {
          nodeEl.appendChild(buildConnectHandle(item.id, anchor));
        });
      }
    } else {
      nodeEl.classList.add("canvas-node--resource");

      const headerEl = document.createElement("div");
      headerEl.className = "canvas-resource-header";

      const typeEl = document.createElement("span");
      typeEl.className = "canvas-resource-type";
      typeEl.textContent = resourceType;

      headerEl.appendChild(iconEl);
      headerEl.appendChild(typeEl);
      headerEl.appendChild(buildRemoveControl(item.id));

      const bodyEl = document.createElement("div");
      bodyEl.className = "canvas-resource-body";
      bodyEl.textContent = item.name;

      nodeEl.appendChild(headerEl);
      nodeEl.appendChild(bodyEl);

      if (isConnectableItem(item)) {
        ["top", "right", "bottom", "left"].forEach((anchor) => {
          nodeEl.appendChild(buildConnectHandle(item.id, anchor));
        });
      }
    }

    canvasLayerEl.appendChild(nodeEl);
  }

  const itemsByLayer = {};
  state.canvasItems.forEach(item => {
    const layer = getVisualLayer(item);
    if (!itemsByLayer[layer]) {
      itemsByLayer[layer] = [];
    }
    itemsByLayer[layer].push(item);
  });

  for (let layer = 0; layer <= 5; layer++) {
    const itemsInLayer = itemsByLayer[layer] || [];
    itemsInLayer.forEach(item => {
      renderNode(item);
    });
  }

  updateCanvasNodeSelection();
  renderCanvasConnections();
}

function renderCanvasView() {
  if (!canvasLayerEl || !canvasGridEl || !canvasViewportEl) {
    return;
  }

  canvasLayerEl.style.transform = `translate(${state.canvasView.x}px, ${state.canvasView.y}px) scale(${state.canvasView.zoom})`;
  if (canvasEdgesEl) {
    canvasEdgesEl.style.transform = "none";
  }

  canvasViewportEl.style.setProperty("--canvas-world-width", `${CANVAS_WORLD.width}px`);
  canvasViewportEl.style.setProperty("--canvas-world-height", `${CANVAS_WORLD.height}px`);
  canvasViewportEl.style.setProperty("--canvas-world-screen-x", `${state.canvasView.x}px`);
  canvasViewportEl.style.setProperty("--canvas-world-screen-y", `${state.canvasView.y}px`);
  canvasViewportEl.style.setProperty("--canvas-world-scale", `${state.canvasView.zoom}`);

  if (canvasZoomLabelEl) {
    canvasZoomLabelEl.textContent = `${Math.round(state.canvasView.zoom * 100)}%`;
  }

  renderCanvasConnections();
}

function updateCanvasNodeSelection() {
  if (!canvasLayerEl) return;

  const nodes = canvasLayerEl.querySelectorAll(".canvas-node");
  nodes.forEach((nodeEl) => {
    const itemId = nodeEl.dataset.itemId;
    if (itemId === state.selectedResource) {
      nodeEl.classList.add("is-selected");
    } else {
      nodeEl.classList.remove("is-selected");
    }
  });
}

function updatePropertyPanel(resourceName) {
  if (!resourceName) {
    if (propertyContentEl) {
      propertyContentEl.innerHTML = "<p>Select a resource to edit properties.</p>";
    }
    return;
  }

  const item = getItemById(resourceName);
  if (!item) {
    if (propertyContentEl) {
      propertyContentEl.innerHTML = "<p>Resource not found.</p>";
    }
    return;
  }

  const html = `
    <div class="property-item">
      <label>Name:</label>
      <input type="text" value="${escapeHtml(item.name)}" data-property="name" />
    </div>
    <div class="property-item">
      <label>Type:</label>
      <input type="text" value="${escapeHtml(item.resourceType)}" data-property="resourceType" readonly />
    </div>
    <div class="property-item">
      <label>Category:</label>
      <input type="text" value="${escapeHtml(item.category || '')}" data-property="category" />
    </div>
  `;

  if (propertyContentEl) {
    propertyContentEl.innerHTML = html;
  }
}

function escapeHtml(value) {
  const div = document.createElement("div");
  div.textContent = value;
  return div.innerHTML;
}

// ===== Interaction Functions =====

function selectCanvasItem(itemId) {
  const item = getItemById(itemId);
  state.selectedResource = item ? item.id : null;
  state.selectedConnectionId = null;
  updatePropertyPanel(state.selectedResource);
  updateCanvasNodeSelection();
  renderCanvasConnections();
}

function setCanvasZoom(nextZoom, clientX, clientY) {
  if (!canvasViewportEl) {
    return;
  }

  const boundedZoom = clamp(nextZoom, CANVAS_ZOOM.min, CANVAS_ZOOM.max);
  if (boundedZoom === state.canvasView.zoom) {
    return;
  }

  const rect = canvasViewportEl.getBoundingClientRect();
  const anchorX = typeof clientX === "number" ? clientX - rect.left : rect.width / 2;
  const anchorY = typeof clientY === "number" ? clientY - rect.top : rect.height / 2;

  const worldX = (anchorX - state.canvasView.x) / state.canvasView.zoom;
  const worldY = (anchorY - state.canvasView.y) / state.canvasView.zoom;

  state.canvasView.zoom = boundedZoom;
  state.canvasView.x = anchorX - worldX * boundedZoom;
  state.canvasView.y = anchorY - worldY * boundedZoom;

  renderCanvasView();
  persistCanvasLocal();
}


function adjustCanvasZoom(direction, clientX, clientY) {
  const nextZoom = state.canvasView.zoom + direction * CANVAS_ZOOM.step;
  setCanvasZoom(nextZoom, clientX, clientY);
}

function createCanvasItem(resource, worldX, worldY) {
  const parentContainer = getContainerAtWorldPoint(worldX, worldY);

  let snappedX = Math.round(worldX / 10) * 10;
  let snappedY = Math.round(worldY / 10) * 10;
  let parentId = null;

  if (parentContainer) {
    const parentWorld = getItemWorldPosition(parentContainer.id);
    snappedX = Math.round((worldX - parentWorld.x - CANVAS_CONTAINER.padding) / 10) * 10;
    snappedY = Math.round((worldY - parentWorld.y - CANVAS_CONTAINER.headerHeight - CANVAS_CONTAINER.padding) / 10) * 10;
    parentId = parentContainer.id;
  }

  const containerResource = isContainerResource(resource);
  const resourceType = String(resource.resourceType || resource.name || "Resource");
  const defaultName = getNextResourceDefaultName();

  const newItem = {
    id: `item-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: defaultName,
    resourceType,
    iconSrc: resource.iconSrc || "/Assets/Icons/resource-default.png",
    category: resource.category || "",
    isContainer: containerResource,
    parentId,
    width: containerResource ? CANVAS_CONTAINER.defaultWidth : undefined,
    height: containerResource ? CANVAS_CONTAINER.defaultHeight : undefined,
    x: snappedX,
    y: snappedY,
    properties: {}
  };

  state.canvasItems.push(newItem);
  renderCanvasItems();
  selectCanvasItem(newItem.id);
  persistCanvasLocal();
}

function removeCanvasItem(itemId) {
  const item = getItemById(itemId);
  if (!item) {
    return;
  }

  const directChildren = getChildrenByParentId(itemId);
  directChildren.forEach((child) => {
    const childWorld = getItemWorldPosition(child.id);
    child.parentId = null;
    child.x = childWorld.x;
    child.y = childWorld.y;
  });

  state.canvasItems = state.canvasItems.filter((candidate) => candidate.id !== itemId);
  state.canvasConnections = state.canvasConnections.filter((connection) => connection.fromId !== itemId && connection.toId !== itemId);

  if (state.selectedResource === itemId) {
    state.selectedResource = null;
    updatePropertyPanel(null);
  }

  if (state.edgeDraft.sourceId === itemId) {
    state.edgeDraft.active = false;
    state.edgeDraft.sourceId = null;
  }

  renderCanvasItems();
  persistCanvasLocal();
}

function clearEdgeDraft() {
  state.edgeDraft.active = false;
  state.edgeDraft.sourceId = null;
  state.edgeDraft.sourceAnchor = "right";
  state.edgeDraft.targetId = null;
  state.edgeDraft.targetAnchor = "left";
}

// ===== Backend Integration =====

async function loadCurrentProject(projectId) {
  try {
    const response = await fetch(`/api/project/${encodeURIComponent(projectId)}`, { cache: "no-store" });
    if (!response.ok) {
      return false;
    }

    const payload = await response.json();
    const project = payload?.project && typeof payload.project === "object" ? payload.project : {};
    const canvasState = payload?.canvasState && typeof payload.canvasState === "object" ? payload.canvasState : {};

    const normalized = {
      ...project,
      canvasStateHash: payload?.stateHash,
      selectedResource: canvasState.selectedResource,
      searchTerm: canvasState.searchTerm,
      canvasView: canvasState.canvasView || createDefaultCanvasView(),
      canvasItems: canvasState.canvasItems || [],
      canvasConnections: canvasState.canvasConnections || []
    };

    state.currentProject = { ...normalized };
    state.canvasView = state.currentProject.canvasView || createDefaultCanvasView();
    state.canvasItems = state.currentProject.canvasItems || [];
    state.canvasConnections = state.currentProject.canvasConnections || [];
    state.selectedResource = state.currentProject.selectedResource || null;

    return true;
  } catch (error) {
    console.error("Failed to load project:", error);
    return false;
  }
}

async function runProjectSaveRequest(options = {}) {
  try {
    flushPendingCanvasEditsForManualSave();

    if (!state.currentProject) {
      console.warn("No current project to save");
      return;
    }

    const payload = {
      projectId: state.currentProject.id,
      project: {
        ...state.currentProject,
        name: state.currentProject.name || "Untitled"
      },
      canvasState: {
        canvasView: state.canvasView,
        canvasItems: state.canvasItems,
        canvasConnections: state.canvasConnections,
        selectedResource: state.selectedResource
      }
    };

    const response = await fetch("/api/project/save", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload)
    });

    if (!response.ok) {
      console.error("Save failed:", response.status);
      return;
    }

    const result = await response.json();
    if (result.success) {
      if (centerSystemMessageEl) {
        centerSystemMessageEl.textContent = "Project saved successfully";
        setTimeout(() => {
          centerSystemMessageEl.textContent = "Space to display system messages and notifications etc";
        }, 3000);
      }
    }
  } catch (error) {
    console.error("Error saving project:", error);
  }
}

// ===== Event Listeners =====

function initializeCanvasInteractions() {
  if (!canvasViewportEl || !canvasLayerEl || !canvasGridEl) {
    return;
  }

  // Drag and drop resources from left panel
  canvasViewportEl.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });

  canvasViewportEl.addEventListener("drop", (event) => {
    event.preventDefault();

    const payload = event.dataTransfer.getData("application/json");
    if (!payload) {
      return;
    }

    try {
      const resource = JSON.parse(payload);
      const world = toWorldPoint(event.clientX, event.clientY);
      createCanvasItem(resource, world.x, world.y);
    } catch (error) {
      console.error("Error processing drop:", error);
    }
  });

  // Mouse wheel zoom
  canvasViewportEl.addEventListener("wheel", (event) => {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      const direction = event.deltaY < 0 ? 1 : -1;
      adjustCanvasZoom(direction, event.clientX, event.clientY);
    }
  }, { passive: false });

  // Pan and select
  canvasViewportEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0 && event.button !== 1) {
      return;
    }

    const panningWithMiddleButton = event.button === 1;
    const panningWithSpaceKey = event.button === 0 && canvasInteraction.spacePanMode;
    const clickedNode = event.target.closest(".canvas-node");

    if (!panningWithMiddleButton && clickedNode && !panningWithSpaceKey) {
      return;
    }

    event.preventDefault();
    state.selectedConnectionId = null;
    state.selectedResource = null;
    updatePropertyPanel(null);
    updateCanvasNodeSelection();
    renderCanvasConnections();
    canvasInteraction.isPanning = true;
    canvasViewportEl.classList.add("is-panning");
    canvasInteraction.panOriginX = event.clientX;
    canvasInteraction.panOriginY = event.clientY;
    canvasInteraction.viewOriginX = state.canvasView.x;
    canvasInteraction.viewOriginY = state.canvasView.y;
  });

  document.addEventListener("mousemove", (event) => {
    if (!canvasInteraction.isPanning) {
      return;
    }

    const deltaX = event.clientX - canvasInteraction.panOriginX;
    const deltaY = event.clientY - canvasInteraction.panOriginY;
    state.canvasView.x = canvasInteraction.viewOriginX + deltaX;
    state.canvasView.y = canvasInteraction.viewOriginY + deltaY;
    renderCanvasView();
  });

  document.addEventListener("mouseup", () => {
    if (canvasInteraction.isPanning) {
      canvasInteraction.isPanning = false;
      canvasViewportEl.classList.remove("is-panning");
      persistCanvasLocal();
    }
  });

  // Spacebar for pan mode
  document.addEventListener("keydown", (event) => {
    if (event.code === "Space" && !canvasInteraction.spacePanMode) {
      event.preventDefault();
      canvasInteraction.spacePanMode = true;
      if (canvasViewportEl) {
        canvasViewportEl.style.cursor = "grab";
      }
    }
  });

  document.addEventListener("keyup", (event) => {
    if (event.code === "Space" && canvasInteraction.spacePanMode) {
      canvasInteraction.spacePanMode = false;
      if (canvasViewportEl) {
        canvasViewportEl.style.cursor = "";
      }
    }
  });

  // Connect handle
  canvasLayerEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0) {
      return;
    }

    if (canvasInteraction.spacePanMode) {
      return;
    }

    const handleEl = event.target.closest(".canvas-connect-handle");
    if (!handleEl) {
      return;
    }

    const sourceItem = getItemById(handleEl.dataset.itemId);
    if (!isConnectableItem(sourceItem)) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    state.edgeDraft.active = true;
    state.edgeDraft.sourceId = handleEl.dataset.itemId;
    state.edgeDraft.sourceAnchor = handleEl.dataset.anchor || "right";
    state.edgeDraft.targetId = null;
    state.edgeDraft.targetAnchor = "left";
    state.edgeDraft.endClientX = event.clientX;
    state.edgeDraft.endClientY = event.clientY;
    state.selectedConnectionId = null;
    renderCanvasConnections();
    updateCanvasNodeSelection();
  });

  // Drag edge end
  document.addEventListener("mousemove", (event) => {
    if (state.edgeDraft.active) {
      state.edgeDraft.endClientX = event.clientX;
      state.edgeDraft.endClientY = event.clientY;
      renderCanvasConnections();
    }
  });

  // Release edge
  document.addEventListener("mouseup", (event) => {
    if (state.edgeDraft.active && state.edgeDraft.sourceId) {
      const handleEl = event.target.closest(".canvas-connect-handle");
      if (handleEl) {
        const targetId = handleEl.dataset.itemId;
        const targetItem = getItemById(targetId);
        if (targetItem && isConnectableItem(targetItem) && targetId !== state.edgeDraft.sourceId) {
          const connection = {
            id: `conn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
            fromId: state.edgeDraft.sourceId,
            toId: targetId,
            sourceAnchor: state.edgeDraft.sourceAnchor,
            targetAnchor: handleEl.dataset.anchor || "left",
            direction: "forward"
          };
          state.canvasConnections.push(connection);
          persistCanvasLocal();
        }
      }
      clearEdgeDraft();
      renderCanvasConnections();
    }
  });

  // Node click to select
  canvasLayerEl.addEventListener("click", (event) => {
    if (canvasInteraction.spacePanMode) {
      return;
    }

    const nodeEl = event.target.closest(".canvas-node");
    if (!nodeEl) {
      return;
    }

    const itemId = nodeEl.dataset.itemId;
    selectCanvasItem(itemId);
  });

  // Remove button
  canvasLayerEl.addEventListener("click", (event) => {
    const removeBtn = event.target.closest(".canvas-node-remove");
    if (!removeBtn) {
      return;
    }

    event.stopPropagation();
    const itemId = removeBtn.dataset.itemId;
    removeCanvasItem(itemId);
  });
}

// ===== UI Button Handlers =====

function setupButtonHandlers() {
  // Zoom controls
  if (canvasZoomInBtn) {
    canvasZoomInBtn.addEventListener("click", () => {
      if (canvasViewportEl) {
        const rect = canvasViewportEl.getBoundingClientRect();
        adjustCanvasZoom(1, rect.left + rect.width / 2, rect.top + rect.height / 2);
      }
    });
  }

  if (canvasZoomOutBtn) {
    canvasZoomOutBtn.addEventListener("click", () => {
      if (canvasViewportEl) {
        const rect = canvasViewportEl.getBoundingClientRect();
        adjustCanvasZoom(-1, rect.left + rect.width / 2, rect.top + rect.height / 2);
      }
    });
  }

  if (canvasResetViewBtn) {
    canvasResetViewBtn.addEventListener("click", () => {
      state.canvasView = createDefaultCanvasView();
      renderCanvasView();
      persistCanvasLocal();
    });
  }

  // Save button
  if (btnProjectSave) {
    btnProjectSave.addEventListener("click", () => {
      runProjectSaveRequest();
    });
  }

  // Back button
  if (btnBackProjects) {
    btnBackProjects.addEventListener("click", () => {
      window.location.href = "./index.html";
    });
  }

  // Tab switching
  tabsElements.forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabId = tab.dataset.tab;
      
      // Remove active from all tabs
      tabsElements.forEach(t => t.classList.remove("is-active"));
      tabPanelsElements.forEach(p => {
        p.classList.add("is-hidden");
        p.hidden = true;
      });

      // Add active to clicked tab
      tab.classList.add("is-active");
      const panel = document.getElementById(`panel-${tabId}`);
      if (panel) {
        panel.classList.remove("is-hidden");
        panel.hidden = false;
      }
    });
  });
}

// ===== Resource List Rendering =====

function renderResourceList() {
  if (!resourceListEl) return;

  const searchTerm = (searchInput?.value || "").toLowerCase();
  
  // For now, show a placeholder
  // In full implementation, this would load from resource catalog
  resourceListEl.innerHTML = `
    <div class="resource-item" draggable="true" data-resource='{"name":"App Service","resourceType":"Azure App Service","category":"Compute","iconSrc":"/Assets/Icons/compute/app-services.svg"}'>
      <img src="/Assets/Icons/compute/app-services.svg" alt="App Service" />
      <span>App Service</span>
    </div>
    <div class="resource-item" draggable="true" data-resource='{"name":"Virtual Machine","resourceType":"Azure VM","category":"Compute","iconSrc":"/Assets/Icons/compute/virtual-machine.svg"}'>
      <img src="/Assets/Icons/compute/virtual-machine.svg" alt="Virtual Machine" />
      <span>Virtual Machine</span>
    </div>
    <div class="resource-item" draggable="true" data-resource='{"name":"Key Vault","resourceType":"Azure Key Vault","category":"Security","iconSrc":"/Assets/Icons/security/key-vault.svg"}'>
      <img src="/Assets/Icons/security/key-vault.svg" alt="Key Vault" />
      <span>Key Vault</span>
    </div>
  `;

  // Setup drag events
  const resourceItems = resourceListEl.querySelectorAll(".resource-item");
  resourceItems.forEach((item) => {
    item.addEventListener("dragstart", (event) => {
      try {
        const resourceJson = item.dataset.resource;
        event.dataTransfer.effectAllowed = "copy";
        event.dataTransfer.setData("application/json", resourceJson);
      } catch (error) {
        console.error("Error setting drag data:", error);
      }
    });
  });
}

// ===== Initialization =====

function initializeCanvas() {
  initializeCanvasInteractions();
  setupButtonHandlers();
  renderResourceList();

  // Setup search
  if (searchInput) {
    searchInput.addEventListener("input", () => {
      renderResourceList();
    });
  }

  // Initial render
  renderCanvasView();
  renderCanvasItems();
  renderResourceList();

  // Load project from URL
  const urlParams = new URLSearchParams(window.location.search);
  const projectId = urlParams.get("projectId");
  
  if (projectId) {
    loadCurrentProject(projectId).then((success) => {
      if (success) {
        renderCanvasView();
        renderCanvasItems();
        
        // Update project name display
        if (projectNameDisplay && state.currentProject) {
          projectNameDisplay.textContent = state.currentProject.name || "Project";
        }

        if (centerSystemMessageEl) {
          centerSystemMessageEl.textContent = `Loaded project: ${state.currentProject.name}`;
        }
      }
    });
  }

  // Auto-save periodically
  setInterval(() => {
    if (state.currentProject) {
      persistCanvasLocal();
    }
  }, AUTOSAVE_INTERVAL_MS);
}

// Start initialization when DOM is ready
if (document.readyState === "loading") {
  document.addEventListener("DOMContentLoaded", initializeCanvas);
} else {
  initializeCanvas();
}