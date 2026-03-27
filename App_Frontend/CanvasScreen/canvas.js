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
const btnReachOut = document.getElementById("btn-reach-out");
const btnBackProjects = document.getElementById("btn-back-projects");
const chatRuntimeModelEl = document.getElementById("chat-runtime-model");
const chatRuntimeMcpEl = document.getElementById("chat-runtime-mcp");
const centerSystemMessageEl = document.getElementById("center-system-message");
const centerStatusBoundaryEl = document.getElementById("center-status-boundary");
const tabsElements = document.querySelectorAll('.tab[role="tab"]');
const tabPanelsElements = document.querySelectorAll('.tab-pane[role="tabpanel"]');

// Chat panel elements
const chatHistoryEl = document.getElementById("chat-history");
const chatInputEl = document.getElementById("chat-input");
const chatSendBtn = document.getElementById("chat-send");
const tipsContentEl = document.getElementById("tips-content");

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

// ===== Chat State =====
let chatAgentState = null;
let chatRequestInFlight = false;
const chatWelcomeMarkup = chatHistoryEl ? chatHistoryEl.innerHTML : "";

// ===== Validation / Tips State =====
let validationRunInFlight = false;
let validationResult = null;
let tipsExpandedSections = new Set();
const tipsInitialMarkup = tipsContentEl ? tipsContentEl.innerHTML : "";
let centerMessageFlashTimer = null;

// ===== Helper Functions =====

function getItemById(itemId) {
  return state.canvasItems.find((candidate) => candidate.id === itemId) || null;
}

function setCenterSystemMessage(message, { flash = true, type = "" } = {}) {
  if (!centerSystemMessageEl) {
    return;
  }

  const safeMessage = String(message || "").trim();
  if (!safeMessage) {
    centerSystemMessageEl.innerHTML = "";
    return;
  }

  centerSystemMessageEl.innerHTML = `<div class="message-item${type ? ` message-item--${type}` : ""}">${escapeHtml(safeMessage)}</div>`;

  if (!flash || !centerStatusBoundaryEl) {
    return;
  }

  centerStatusBoundaryEl.classList.remove("is-message-flash");
  void centerStatusBoundaryEl.offsetWidth;
  centerStatusBoundaryEl.classList.add("is-message-flash");

  if (centerMessageFlashTimer) {
    clearTimeout(centerMessageFlashTimer);
  }
  centerMessageFlashTimer = setTimeout(() => {
    centerStatusBoundaryEl.classList.remove("is-message-flash");
  }, 760);
}

function showReachOutMenu() {
  // Create a temporary menu for contact options
  const menuEl = document.createElement("div");
  menuEl.style.cssText = `
    position: fixed;
    top: 60px;
    right: 150px;
    background: white;
    border: 1px solid #ccc;
    border-radius: 4px;
    box-shadow: 0 2px 8px rgba(0,0,0,0.15);
    z-index: 10000;
    min-width: 200px;
    animation: slideDown 0.2s ease-out;
  `;
  
  const styleSheet = document.createElement("style");
  styleSheet.textContent = `
    @keyframes slideDown {
      from {
        opacity: 0;
        transform: translateY(-8px);
      }
      to {
        opacity: 1;
        transform: translateY(0);
      }
    }
  `;
  document.head.appendChild(styleSheet);
  
  // Website option
  const websiteBtn = document.createElement("a");
  websiteBtn.href = "https://mohit13.com/";
  websiteBtn.target = "_blank";
  websiteBtn.rel = "noopener noreferrer";
  websiteBtn.style.cssText = `
    display: block;
    padding: 12px 16px;
    text-decoration: none;
    color: #333;
    font-size: 14px;
    border-bottom: 1px solid #eee;
    cursor: pointer;
    transition: background-color 0.2s;
  `;
  websiteBtn.textContent = "📌 Visit Website";
  websiteBtn.onmouseover = () => { websiteBtn.style.backgroundColor = "#f5f5f5"; };
  websiteBtn.onmouseout = () => { websiteBtn.style.backgroundColor = "transparent"; };
  
  // Email option
  const emailBtn = document.createElement("a");
  emailBtn.href = "mailto:mohit13@outlook.com";
  emailBtn.style.cssText = `
    display: block;
    padding: 12px 16px;
    text-decoration: none;
    color: #333;
    font-size: 14px;
    cursor: pointer;
    transition: background-color 0.2s;
  `;
  emailBtn.textContent = "✉️ Send Email";
  emailBtn.onmouseover = () => { emailBtn.style.backgroundColor = "#f5f5f5"; };
  emailBtn.onmouseout = () => { emailBtn.style.backgroundColor = "transparent"; };
  
  menuEl.appendChild(websiteBtn);
  menuEl.appendChild(emailBtn);
  document.body.appendChild(menuEl);
  
  // Close menu when clicking outside
  const closeMenu = (e) => {
    if (!menuEl.contains(e.target) && e.target !== btnReachOut && !btnReachOut.contains(e.target)) {
      menuEl.remove();
      document.removeEventListener("click", closeMenu);
    }
  };
  
  setTimeout(() => {
    document.addEventListener("click", closeMenu);
  }, 50);
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
    const width = rect.width / state.canvasView.zoom;
    const height = rect.height / state.canvasView.zoom;
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
    width: item.viewMode === "icon" ? 208 : 180,
    height: item.viewMode === "icon" ? 208 : 120
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

      if (item.viewMode === "icon") {
        nodeEl.classList.add("icon-view");
        const iconViewEl = document.createElement("div");
        iconViewEl.className = "canvas-resource-icon-view";
        const iconOnlyEl = document.createElement("img");
        iconOnlyEl.src = item.iconSrc || "/Assets/Icons/resource-default.png";
        iconOnlyEl.alt = `${resourceType} icon`;
        iconOnlyEl.draggable = false;
        iconViewEl.appendChild(iconOnlyEl);
        nodeEl.appendChild(iconViewEl);
        nodeEl.appendChild(buildRemoveControl(item.id));
      } else {
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
      }

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
  updatePropertyPanelForSelection();
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
      setCenterSystemMessage("Project saved successfully", { flash: true, type: "ok" });
      setTimeout(() => {
        setCenterSystemMessage("Space to display system messages and notifications etc", { flash: false });
      }, 3000);
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

  // Validate button
  if (btnValidate) {
    btnValidate.addEventListener("click", () => {
      runValidation();
    });
  }

  // Reach Out button - show contact options
  if (btnReachOut) {
    btnReachOut.addEventListener("click", () => {
      showReachOutMenu();
    });
  }

  // Tab switching — on switching to chat, load history; on switching to tips, render panel
  tabsElements.forEach((tab) => {
    tab.addEventListener("click", () => {
      const tabId = tab.dataset.tab;
      setActiveTab(tabId);
      if (tabId === "chat") {
        loadChatHistory();
        loadChatStatus();
      }
      if (tabId === "tips") {
        renderTipsPanel();
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

// ===== AI Chat Functions =====

function setActiveTab(tabId) {
  tabsElements.forEach(t => t.classList.remove("is-active"));
  tabPanelsElements.forEach(p => { p.classList.add("is-hidden"); p.hidden = true; });
  const tab = document.querySelector(`.tab[data-tab="${tabId}"]`);
  if (tab) tab.classList.add("is-active");
  const panel = document.getElementById(`panel-${tabId}`);
  if (panel) { panel.classList.remove("is-hidden"); panel.hidden = false; }
}

function escapeHtmlChat(value) {
  const d = document.createElement("div");
  d.textContent = value;
  return d.innerHTML;
}

function appendChatMessage(text, role = "user", scroll = true) {
  if (!chatHistoryEl) return;
  // Remove welcome banner on first real message
  const welcome = chatHistoryEl.querySelector(".chat-welcome");
  if (welcome) welcome.remove();

  const el = document.createElement("div");
  el.className = `chat-message chat-message--${role}`;
  el.textContent = text;
  chatHistoryEl.appendChild(el);
  if (scroll) chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  return el;
}

function appendLoadingBubble() {
  if (!chatHistoryEl) return null;
  const el = document.createElement("div");
  el.className = "chat-message chat-message--assistant chat-message--loading";
  el.innerHTML = '<span class="chat-dots"><span></span><span></span><span></span></span>';
  chatHistoryEl.appendChild(el);
  chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  return el;
}

function setChatBusy(isBusy) {
  chatRequestInFlight = Boolean(isBusy);
  if (chatSendBtn) chatSendBtn.disabled = chatRequestInFlight;
  if (chatInputEl) chatInputEl.disabled = chatRequestInFlight;
}

async function loadChatHistory() {
  if (!chatHistoryEl || !state.currentProject?.id) return;
  try {
    const res = await fetch(`/api/chat/architecture/history?projectId=${encodeURIComponent(state.currentProject.id)}`);
    if (!res.ok) return;
    const payload = await res.json();
    const messages = Array.isArray(payload?.messages) ? payload.messages : [];
    const valid = messages.filter(m => (m.role === "user" || m.role === "assistant") && String(m.content || "").trim());
    if (!valid.length) return;
    chatHistoryEl.innerHTML = "";
    valid.forEach(m => appendChatMessage(m.content, m.role, false));
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  } catch {
    // Keep welcome state on error
  }
}

async function loadChatStatus() {
  try {
    const projectId = state.currentProject?.id ? `?projectId=${encodeURIComponent(state.currentProject.id)}` : "";
    const res = await fetch(`/api/chat/architecture/status${projectId}`);
    if (!res.ok) return;
    const payload = await res.json();
    const model = payload?.model || {};
    const mcp = payload?.connections?.azureMcp || {};
    if (chatRuntimeModelEl) {
      chatRuntimeModelEl.textContent = String(model.activeModel || model.configuredModel || "—").trim() || "—";
    }
    if (chatRuntimeMcpEl) {
      chatRuntimeMcpEl.textContent = mcp.connected ? "Connected" : (mcp.configured ? "Configured" : "Unavailable");
    }
  } catch {
    if (chatRuntimeModelEl) chatRuntimeModelEl.textContent = "Unavailable";
    if (chatRuntimeMcpEl) chatRuntimeMcpEl.textContent = "Unknown";
  }
}

async function sendChatMessage() {
  if (!chatInputEl || chatRequestInFlight) return;
  const message = chatInputEl.value.trim();
  if (!message) return;

  appendChatMessage(message, "user");
  chatInputEl.value = "";
  setChatBusy(true);
  const loadingEl = appendLoadingBubble();

  try {
    const res = await fetch("/api/chat/architecture", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        message,
        projectId: state.currentProject?.id || null,
        agentState: chatAgentState || null,
      }),
    });
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      throw new Error(payload?.detail || "AI chat request failed.");
    }
    if (loadingEl) loadingEl.remove();
    chatAgentState = payload?.agentState || null;
    appendChatMessage(String(payload?.message || "I could not generate a response."), "assistant");
  } catch (err) {
    if (loadingEl) loadingEl.remove();
    appendChatMessage(err?.message || "Unable to complete AI chat request.", "assistant");
  } finally {
    setChatBusy(false);
    if (chatInputEl) chatInputEl.focus();
  }
}

if (chatSendBtn) chatSendBtn.addEventListener("click", sendChatMessage);
if (chatInputEl) {
  chatInputEl.addEventListener("keydown", (e) => {
    if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); sendChatMessage(); }
  });
}

// ===== Validation / Tips Functions =====

function buildCanvasSnapshot() {
  const items = state.canvasItems || [];
  const connections = state.canvasConnections || [];
  return {
    canvasItems: items,
    canvasConnections: connections,
    summary: {
      totalResources: items.length,
      totalConnections: connections.length,
      resourceTypes: [...new Set(items.map(i => i.resourceType || i.name || "Unknown"))],
    },
  };
}

// ---- Tips structural helpers (mirrors old canvas arrangement) ----

function renderTipsGroup(key, title, count, bodyHtml, defaultExpanded = false, nested = false) {
  const isOpen = tipsExpandedSections.has(key) || defaultExpanded;
  const titleText = count != null ? `${title} (${count})` : title;
  const classes = ["validation-group", nested ? "validation-group--nested" : ""].filter(Boolean).join(" ");
  return `
    <section class="${classes}">
      <button type="button" class="validation-group__toggle" data-tips-toggle="${escapeHtmlChat(key)}" aria-expanded="${isOpen}">
        <span class="validation-group__title">${escapeHtmlChat(titleText)}</span>
        <span class="validation-group__chevron" aria-hidden="true">${isOpen ? "▾" : "▸"}</span>
      </button>
      <div class="validation-group__body${isOpen ? "" : " is-hidden"}"${isOpen ? "" : " hidden"}>
        ${bodyHtml}
      </div>
    </section>`;
}

function renderFinding(titleHtml, bodyHtml) {
  return `<article class="validation-finding"><h4 class="validation-finding__title">${titleHtml}</h4>${bodyHtml}</article>`;
}

function renderLineList(entries, emptyMsg = "None identified.", compact = false) {
  const safe = Array.isArray(entries) ? entries.filter(e => String(e || "").trim()) : [];
  if (!safe.length) return `<p class="validation-empty">${escapeHtmlChat(emptyMsg)}</p>`;
  const cls = compact ? "validation-list validation-list--compact" : "validation-list";
  return `<ul class="${cls}">${safe.map(e => `<li>${escapeHtmlChat(String(e))}</li>`).join("")}</ul>`;
}

function renderActionChecklist(items, emptyMsg = "No actions identified.") {
  const safe = Array.isArray(items) ? items.filter(e => String(e || "").trim()) : [];
  if (!safe.length) return `<p class="validation-empty">${escapeHtmlChat(emptyMsg)}</p>`;
  return `<ol class="validation-action-list">${safe.map(e => `<li>&#10003; ${escapeHtmlChat(String(e))}</li>`).join("")}</ol>`;
}

function renderTipsPanel() {
  if (!tipsContentEl) return;

  if (!validationResult) {
    tipsContentEl.innerHTML = tipsInitialMarkup || `<div class="tab-placeholder"><div class="tab-placeholder-icon">&#128161;</div><p class="tab-placeholder-title">Architecture Tips</p><p class="tab-placeholder-sub">Click <strong>Validate</strong> in the toolbar to analyse your architecture.</p></div>`;
    return;
  }

  if (validationResult.errorMessage) {
    tipsContentEl.innerHTML = `<div class="vr-error"><strong>Validation failed</strong><p>${escapeHtmlChat(validationResult.errorMessage)}</p></div>`;
    return;
  }

  const r = validationResult;
  const archSummary        = String(r.architecture_summary || "").trim();
  const detectedServices   = Array.isArray(r.detected_services) ? r.detected_services : [];
  const maturity           = r.architecture_maturity && typeof r.architecture_maturity === "object" ? r.architecture_maturity : {};
  const pillarAssessment   = r.pillar_assessment && typeof r.pillar_assessment === "object" ? r.pillar_assessment : {};
  const priorityItems      = Array.isArray(r.priority_improvements) ? r.priority_improvements : [];
  const configIssues       = Array.isArray(r.configuration_issues) ? r.configuration_issues : [];
  const antiPatterns       = Array.isArray(r.architecture_antipatterns) ? r.architecture_antipatterns : [];
  const quickFixes         = Array.isArray(r.quick_configuration_fixes) ? r.quick_configuration_fixes : [];
  const missingCaps        = Array.isArray(r.missing_capabilities) ? r.missing_capabilities : [];
  const recPatterns        = Array.isArray(r.recommended_patterns) ? r.recommended_patterns : [];

  // ---- Runtime strip ----
  const runtimeHtml = [
    `<div class="validation-runtime__row"><span class="validation-runtime__label">Status</span><span class="vr-status-ok">Completed</span></div>`,
    `<div class="validation-runtime__row"><span class="validation-runtime__label">Services detected</span><span class="vr-status-ok">${detectedServices.length}</span></div>`,
    `<div class="validation-runtime__row"><span class="validation-runtime__label">Pillars assessed</span><span class="vr-status-ok">5</span></div>`,
  ].join("");

  // ---- Summary counts strip ----
  const criticalCount = configIssues.length + antiPatterns.length;
  const improvCount   = priorityItems.length;
  const missingCount  = missingCaps.length;
  const summaryHtml = [
    `<span class="validation-summary__item">Config Issues <strong>${criticalCount}</strong></span>`,
    `<span class="validation-summary__item">Improvements <strong>${improvCount}</strong></span>`,
    `<span class="validation-summary__item">Missing <strong>${missingCount}</strong></span>`,
  ].join("");

  // ---- Section 1: Overview ----
  const overallAssessment = String(maturity.overall_assessment || "").trim();
  const servicesListHtml = detectedServices.length
    ? `<ul class="validation-list">${detectedServices.map(s => `<li>${escapeHtmlChat(s)}</li>`).join("")}</ul>`
    : `<p class="validation-empty">No services detected.</p>`;

  const section1Body = [
    archSummary ? renderFinding("Architecture Summary", `<p class="validation-finding__message">${escapeHtmlChat(archSummary)}</p>`) : "",
    renderFinding("Detected Azure Services", servicesListHtml),
    overallAssessment ? renderFinding("Overall Assessment", `<p class="validation-finding__message">${escapeHtmlChat(overallAssessment)}</p>`) : "",
  ].join("");

  // ---- Section 2: Architecture Maturity ----
  const maturityDims = ["reliability", "security", "observability", "scalability", "operational_maturity"];
  const maturityRows = maturityDims.map(d => {
    const level = String(maturity[d] || "Low").trim();
    const label = d.replace(/_/g, " ").replace(/\b\w/g, c => c.toUpperCase());
    return `<tr><td>${escapeHtmlChat(label)}</td><td class="vr-maturity-cell vr-maturity--${level.toLowerCase()}">${escapeHtmlChat(level)}</td></tr>`;
  }).join("");
  const maturityTableHtml = `
    <div class="validation-score-table-wrap">
      <table class="validation-score-table">
        <thead><tr><th>Dimension</th><th>Maturity</th></tr></thead>
        <tbody>${maturityRows}</tbody>
      </table>
    </div>`;
  const section2Body = renderFinding("Maturity by Dimension", maturityTableHtml);

  // ---- Section 3: Pillar Scores & Per-Pillar Review ----
  const pillarKeys   = ["reliability", "security", "cost_optimization", "operational_excellence", "performance_efficiency"];
  const pillarLabels = { reliability: "Reliability", security: "Security", cost_optimization: "Cost Optimization", operational_excellence: "Operational Excellence", performance_efficiency: "Performance" };

  const pillarScoreRows = pillarKeys.map(p => {
    const pa = pillarAssessment[p] && typeof pillarAssessment[p] === "object" ? pillarAssessment[p] : {};
    const score = Number(pa.score || 0);
    const icon = score >= 75 ? "✅" : score >= 50 ? "⚠️" : "🔴";
    const recs = Array.isArray(pa.recommendations) ? pa.recommendations : [];
    const topNote = recs.length ? String(recs[0]?.title || recs[0] || "").trim() : "—";
    return `<tr><td><strong>${escapeHtmlChat(pillarLabels[p])}</strong></td><td>${icon} ${score}/100</td><td>${escapeHtmlChat(topNote)}</td></tr>`;
  }).join("");

  const scoreTableHtml = `
    <div class="validation-score-table-wrap">
      <table class="validation-score-table">
        <thead><tr><th>Pillar</th><th>Score</th><th>Leading Note</th></tr></thead>
        <tbody>${pillarScoreRows}</tbody>
      </table>
    </div>`;

  // Per-pillar nested groups
  const pillarGroupsHtml = pillarKeys.map(p => {
    const pa = pillarAssessment[p] && typeof pillarAssessment[p] === "object" ? pillarAssessment[p] : {};
    const strengths = Array.isArray(pa.strengths) ? pa.strengths : [];
    const weaknesses = Array.isArray(pa.weaknesses) ? pa.weaknesses : [];
    const recs = Array.isArray(pa.recommendations) ? pa.recommendations : [];
    const recLines = recs.map(rec => {
      const t = String(rec?.title || rec || "").trim();
      const d = String(rec?.description || "").trim();
      return d ? `${t} — ${d}` : t;
    });
    const pillarBody = [
      renderFinding("Strengths", renderLineList(strengths, "No strengths recorded.")),
      renderFinding("Weaknesses", renderLineList(weaknesses, "No weaknesses recorded.")),
      renderFinding("Recommendations", renderLineList(recLines, "No recommendations recorded.")),
    ].join("");
    return renderTipsGroup(`pillar-${p}`, pillarLabels[p], null, pillarBody, false, true);
  }).join("");

  const section3Body = [
    renderFinding("Pillar Scorecard", scoreTableHtml),
    pillarGroupsHtml,
  ].join("");

  // ---- Section 4: Priority Action Plan ----
  const highItems = priorityItems.filter(i => Number(i?.rank || 99) <= 2);
  const midItems  = priorityItems.filter(i => Number(i?.rank || 99) > 2 && Number(i?.rank || 99) <= 4);
  const lowItems  = priorityItems.filter(i => Number(i?.rank || 99) > 4);

  const formatImprovLine = item => {
    const title = String(item?.title || "Improvement").trim();
    const desc  = String(item?.description || "").trim();
    return desc ? `${title} — ${desc}` : title;
  };

  const section4Body = [
    renderFinding("Immediate (Rank 1–2)", renderActionChecklist(highItems.map(formatImprovLine), "No immediate actions identified.")),
    renderFinding("Short-term (Rank 3–4)", renderActionChecklist(midItems.map(formatImprovLine), "No short-term actions identified.")),
    lowItems.length ? renderFinding("Additional", renderLineList(lowItems.map(formatImprovLine))) : "",
  ].join("");

  // ---- Section 5: Configuration Issues & Anti-patterns ----
  const issueFindings = configIssues.map(item => {
    const resource   = String(item?.resource || "Resource").trim();
    const issue      = String(item?.issue || "").trim();
    const impact     = String(item?.impact || "").trim();
    const resolution = String(item?.resolution || "").trim();
    const bodyParts = [
      issue ? `<p class="validation-finding__message">${escapeHtmlChat(issue)}</p>` : "",
      impact ? `<p class="validation-finding__target">Impact: ${escapeHtmlChat(impact)}</p>` : "",
      resolution ? `<p class="validation-finding__target">&#10003; Fix: ${escapeHtmlChat(resolution)}</p>` : "",
    ].join("");
    return renderFinding(escapeHtmlChat(resource), bodyParts);
  }).join("");

  const antiPatternFindings = antiPatterns.map(item => {
    const name = String(item?.name || "Anti-pattern").trim();
    const risk = String(item?.risk || "").trim();
    const rec  = String(item?.recommendation || "").trim();
    const bodyParts = [
      risk ? `<p class="validation-finding__message">${escapeHtmlChat(risk)}</p>` : "",
      rec  ? `<p class="validation-finding__target">&#10003; ${escapeHtmlChat(rec)}</p>` : "",
    ].join("");
    return renderFinding(escapeHtmlChat(name), bodyParts);
  }).join("");

  const quickFixLines = quickFixes.map(item => {
    const t = String(item?.title || "").trim();
    const r = String(item?.resource || "").trim();
    const target = String(item?.target_state || "").trim();
    return r ? `${r}: ${t}${target ? ` → ${target}` : ""}` : t;
  });

  const section5Body = [
    configIssues.length
      ? `${issueFindings}`
      : renderFinding("Configuration Issues", `<p class="validation-empty">No configuration issues detected.</p>`),
    antiPatterns.length
      ? renderTipsGroup("antipatterns", "Architecture Anti-patterns", antiPatterns.length, antiPatternFindings, false, true)
      : "",
    quickFixes.length
      ? renderFinding("Quick Configuration Fixes", renderActionChecklist(quickFixLines))
      : "",
  ].join("");

  // ---- Section 6: Missing Capabilities & Recommended Patterns ----
  const missingFindings = missingCaps.map(item => {
    const cap        = String(item?.capability || item || "").trim();
    const importance = String(item?.importance || "").trim();
    const reason     = String(item?.reason || "").trim();
    const services   = Array.isArray(item?.suggested_services) ? item.suggested_services : [];
    const badge      = importance ? `<span class="vr-importance vr-importance--${importance.toLowerCase()}">${escapeHtmlChat(importance.toUpperCase())}</span> ` : "";
    const bodyParts  = [
      reason ? `<p class="validation-finding__message">${badge}${escapeHtmlChat(reason)}</p>` : (badge ? `<p class="validation-finding__message">${badge}</p>` : ""),
      services.length ? renderLineList(services, "", true) : "",
    ].join("");
    return renderFinding(escapeHtmlChat(cap), bodyParts);
  }).join("");

  const recPatternFindings = recPatterns.map(item => {
    const name     = String(item?.name || "Pattern").trim();
    const reason   = String(item?.reason || "").trim();
    const services = Array.isArray(item?.azure_services) ? item.azure_services : [];
    const bodyParts = [
      reason ? `<p class="validation-finding__message">${escapeHtmlChat(reason)}</p>` : "",
      services.length ? renderLineList(services, "", true) : "",
    ].join("");
    return renderFinding(escapeHtmlChat(name), bodyParts);
  }).join("");

  const section6Body = [
    missingCaps.length
      ? missingFindings
      : renderFinding("Missing Capabilities", `<p class="validation-empty">No missing capabilities detected.</p>`),
    recPatterns.length
      ? renderTipsGroup("recpatterns", "Recommended Patterns", recPatterns.length, recPatternFindings, false, true)
      : "",
  ].join("");

  // ---- Assemble ----
  const groupsHtml = [
    renderTipsGroup("overview",      "Overview",                            null,                  section1Body, true),
    renderTipsGroup("maturity",      "Architecture Maturity",               maturityDims.length,   section2Body, true),
    renderTipsGroup("pillars",       "Pillar Scores & Review",              pillarKeys.length,     section3Body, true),
    renderTipsGroup("actions",       "Priority Action Plan",                priorityItems.length,  section4Body, true),
    renderTipsGroup("config",        "Configuration Issues & Anti-patterns", configIssues.length + antiPatterns.length, section5Body, false),
    renderTipsGroup("missing",       "Missing Capabilities & Patterns",     missingCaps.length + recPatterns.length,  section6Body, false),
  ].join("");

  tipsContentEl.innerHTML = `
    <div class="validation-runtime">${runtimeHtml}</div>
    <div class="validation-summary">${summaryHtml}</div>
    <div class="validation-groups validation-report">${groupsHtml}</div>`;

  // Wire toggle buttons
  tipsContentEl.querySelectorAll("[data-tips-toggle]").forEach(btn => {
    btn.addEventListener("click", () => {
      const key = btn.dataset.tipsToggle;
      if (tipsExpandedSections.has(key)) {
        tipsExpandedSections.delete(key);
      } else {
        tipsExpandedSections.add(key);
      }
      renderTipsPanel();
    });
  });
}

async function runValidation() {
  if (validationRunInFlight || !state.currentProject?.id) {
    if (!state.currentProject?.id) {
      setActiveTab("tips");
      if (tipsContentEl) tipsContentEl.innerHTML = `<div class="vr-error"><strong>No project loaded</strong><p>Please open a project before running validation.</p></div>`;
    }
    return;
  }

  validationRunInFlight = true;
  validationResult = null;
  tipsExpandedSections = new Set(["overview", "maturity", "pillars", "actions"]);

  // Show running state and switch to tips tab
  setActiveTab("tips");
  if (tipsContentEl) {
    tipsContentEl.innerHTML = `<div class="tips-running"><div class="tips-running-icon">&#9881;</div><p><strong>Validating architecture…</strong></p><p class="tips-running-sub">Analysing against the Azure Well-Architected Framework.</p></div>`;
  }
  if (btnValidate) { btnValidate.textContent = "Validating…"; btnValidate.disabled = true; }

  try {
    const snapshot = buildCanvasSnapshot();
    const projectDescription = String(state.currentProject?.applicationDescription || "").trim();
    const res = await fetch(`/api/project/${encodeURIComponent(state.currentProject.id)}/architecture/validation/run`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        canvasState: snapshot,
        projectDescription,
        projectId: state.currentProject.id,
        projectName: state.currentProject.name || "Project",
      }),
    });
    const payload = await res.json().catch(() => null);
    if (!res.ok) {
      throw new Error(payload?.detail || "Validation request failed.");
    }
    validationResult = payload && typeof payload === "object" ? payload : {};
  } catch (err) {
    validationResult = { errorMessage: err?.message || "Architecture validation failed." };
  } finally {
    validationRunInFlight = false;
    if (btnValidate) { btnValidate.textContent = "Validate"; btnValidate.disabled = false; }
    renderTipsPanel();
  }
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
  updatePropertyPanelForSelection();
  loadChatStatus();

  // Load project from URL
  const urlParams = new URLSearchParams(window.location.search);
  const projectId = urlParams.get("projectId");
  
  if (projectId) {
    loadCurrentProject(projectId).then((success) => {
      if (success) {
        renderCanvasView();
        renderCanvasItems();
        updatePropertyPanelForSelection();
        
        // Update project name display
        if (projectNameDisplay && state.currentProject) {
          projectNameDisplay.textContent = state.currentProject.name || "Project";
        }

        setCenterSystemMessage(`Loaded project: ${state.currentProject.name}`, { flash: true });
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