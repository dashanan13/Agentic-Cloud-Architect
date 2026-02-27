// ===== UI Element References =====
const btnBackProjects = document.getElementById("btn-back-projects");
const btnProjectSave = document.getElementById("btn-project-save");
const btnProjectSettings = document.getElementById("btn-project-settings");
const btnValidate = document.getElementById("btn-validate");
const btnGenBicep = document.getElementById("btn-gen-bicep");
const btnGenTerraform = document.getElementById("btn-gen-terraform");
const projectSaveStatus = document.getElementById("project-save-status");
const projectNamePrefixDisplay = document.getElementById("project-name-prefix-display");
const projectNameDisplay = document.getElementById("project-name-suffix-display");
const projectTimestamp = document.getElementById("project-timestamp");
const resourceListEl = document.getElementById("resource-list");
const searchInput = document.getElementById("search-input");
const selectedResourceNameEl = document.getElementById("selected-resource-name");
const propertyContentEl = document.getElementById("property-content");
const appEl = document.getElementById("app");
const canvasViewportEl = document.getElementById("canvas-viewport");
const canvasGridEl = document.getElementById("canvas-grid");
const canvasLayerEl = document.getElementById("canvas-layer");
const canvasZoomOutBtn = document.getElementById("canvas-zoom-out");
const canvasZoomInBtn = document.getElementById("canvas-zoom-in");
const canvasResetViewBtn = document.getElementById("canvas-reset-view");
const canvasZoomLabelEl = document.getElementById("canvas-zoom-label");
const canvasStatusEl = document.getElementById("canvas-status");
const canvasEdgesEl = document.getElementById("canvas-edges");
const statusLeftWidthEl = document.getElementById("status-left-width");
const statusRightWidthEl = document.getElementById("status-right-width");
const chatHistoryEl = document.getElementById("chat-history");
const chatInputEl = document.getElementById("chat-input");
const chatSendBtn = document.getElementById("chat-send");
const tabGroups = Array.from(document.querySelectorAll('[role="tablist"]'))
  .map((tabListEl) => {
    const groupTabs = Array.from(tabListEl.querySelectorAll('.tab[role="tab"]'));
    if (!groupTabs.length) {
      return null;
    }

    const groupPanels = new Map();
    groupTabs.forEach((tab) => {
      const panelId = tab.getAttribute("aria-controls");
      const panelEl = panelId ? document.getElementById(panelId) : null;
      if (panelEl && tab.dataset.tab) {
        groupPanels.set(tab.dataset.tab, panelEl);
      }
    });

    return {
      tabs: groupTabs,
      panels: groupPanels
    };
  })
  .filter(Boolean);

function readCssPxVar(variableName, fallback) {
  const rootStyle = getComputedStyle(document.documentElement);
  const rawValue = rootStyle.getPropertyValue(variableName).trim();
  const parsedValue = Number.parseFloat(rawValue);
  return Number.isFinite(parsedValue) ? parsedValue : fallback;
}

const layoutConfig = {
  leftDefault: readCssPxVar("--layout-left-default", 180),
  leftMin: readCssPxVar("--layout-left-min", 160),
  leftMax: readCssPxVar("--layout-left-max", 480),
  rightDefault: readCssPxVar("--layout-right-default", 360),
  rightMin: readCssPxVar("--layout-right-min", 360),
  rightMax: readCssPxVar("--layout-right-max", 560),
  bottomDefault: readCssPxVar("--layout-bottom-default", 130),
  bottomMin: readCssPxVar("--layout-bottom-min", 120),
  bottomMax: readCssPxVar("--layout-bottom-max", 380),
  bottomRightDefault: readCssPxVar("--layout-bottom-right-default", 220),
  bottomRightMin: readCssPxVar("--layout-bottom-right-min", 200),
  bottomRightMax: readCssPxVar("--layout-bottom-right-max", 520)
};

// ===== State =====
const cloudCatalogs = {};
const state = {
  projects: [],
  currentProject: null,
  leftWidth: layoutConfig.leftDefault,
  rightWidth: layoutConfig.rightDefault,
  bottomHeight: layoutConfig.bottomDefault,
  bottomRightWidth: layoutConfig.bottomRightDefault,
  selectedResource: null,
  searchTerm: "",
  canvasView: {
    x: 0,
    y: 0,
    zoom: 1
  },
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
  }
};

const canvasInteraction = {
  isPanning: false,
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

const constraints = {
  leftMin: layoutConfig.leftMin,
  leftMax: layoutConfig.leftMax,
  rightMin: layoutConfig.rightMin,
  rightMax: layoutConfig.rightMax,
  bottomMin: layoutConfig.bottomMin,
  bottomMax: layoutConfig.bottomMax,
  bottomRightMin: layoutConfig.bottomRightMin,
  bottomRightMax: layoutConfig.bottomRightMax
};

const MAX_PROJECT_NAME_LENGTH = 27;
const AUTOSAVE_INTERVAL_MS = 60000;
const CANVAS_ZOOM = {
  min: 0.05,
  max: 3,
  step: 0.05,
  grid: 40
};

const CANVAS_CONTAINER = {
  minWidth: 220,
  minHeight: 140,
  defaultWidth: 360,
  defaultHeight: 240,
  headerHeight: 32,
  padding: 12
};

function normalizeLabel(value) {
  return String(value || "").trim().toLowerCase();
}

function isContainerResource(resource) {
  const name = normalizeLabel(resource?.name);
  const containerTerms = [
    "management group",
    "management groups",
    "subscription",
    "subscriptions",
    "resource group",
    "resource groups",
    "virtual network",
    "virtual networks",
    "vnet",
    "subnet",
    "subnets"
  ];

  return containerTerms.some((term) => name.includes(term)) || name === "network";
}

function isLogicalOnlyContainer(item) {
  const name = normalizeLabel(item?.resourceType || item?.name);
  const logicalTerms = ["management group", "subscription", "resource group"];
  return logicalTerms.some((term) => name.includes(term));
}

function isConnectableItem(item) {
  if (!item) {
    return false;
  }

  if (item.isContainer && isLogicalOnlyContainer(item)) {
    return false;
  }

  return true;
}

// ===== Utility Functions =====
function formatTimestamp(ms) {
  const date = new Date(ms);
  return date.toLocaleString();
}

function titleCase(value) {
  return value
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function getCloudIconRoot(cloudName) {
  if (cloudName === "Azure") {
    return "/icons/azure";
  }
  return "";
}

function getProjectPrefix(cloud) {
  return `${cloud}-`;
}

function getMaxSuffixLength(cloud) {
  return Math.max(1, MAX_PROJECT_NAME_LENGTH - getProjectPrefix(cloud).length);
}

function splitProjectName(cloud, fullName) {
  const safeCloud = ["Azure", "AWS", "GCP"].includes(cloud) ? cloud : "Azure";
  const prefix = getProjectPrefix(safeCloud);
  let suffix = (fullName || "").trim();

  if (suffix.toLowerCase().startsWith(prefix.toLowerCase())) {
    suffix = suffix.slice(prefix.length);
  }

  suffix = suffix.replace(/^[-\s]+/, "").trim();
  suffix = suffix.slice(0, getMaxSuffixLength(cloud));

  return {
    prefix,
    suffix
  };
}

function getNextResourceDefaultName() {
  const pattern = /^resource\s+(\d+)$/i;
  let maxIndex = 0;

  const allNames = [
    ...state.canvasItems.map((item) => item?.name),
    ...state.canvasConnections.map((connection) => connection?.name)
  ];

  allNames.forEach((name) => {
    const match = pattern.exec(String(name || "").trim());
    if (!match) {
      return;
    }
    maxIndex = Math.max(maxIndex, Number(match[1]) || 0);
  });

  return `Resource ${maxIndex + 1}`;
}

function renderProjectName() {
  if (!state.currentProject) {
    return;
  }

  const { prefix, suffix } = splitProjectName(state.currentProject.cloud, state.currentProject.name);
  if (projectNamePrefixDisplay) {
    projectNamePrefixDisplay.textContent = prefix;
  }
  if (projectNameDisplay) {
    projectNameDisplay.textContent = suffix;
  }
  state.currentProject.name = `${prefix}${suffix}`;
}

function sanitizeProject(project) {
  if (!project || typeof project !== "object") {
    return null;
  }

  const cloud = ["Azure", "AWS", "GCP"].includes(project.cloud) ? project.cloud : null;
  if (!cloud || !project.id) {
    return null;
  }

  const { prefix, suffix } = splitProjectName(cloud, project.name);

  const incomingView = project.canvasView && typeof project.canvasView === "object"
    ? project.canvasView
    : {};
  const incomingItems = Array.isArray(project.canvasItems)
    ? project.canvasItems
    : [];
  const incomingConnections = Array.isArray(project.canvasConnections)
    ? project.canvasConnections
    : [];

  const sanitizedItems = incomingItems
    .map((item, index) => ({
      id: String(item.id || `item-${Date.now()}`),
      name: String(item.name || `Resource ${index + 1}`),
      resourceType: String(item.resourceType || item.type || item.name || "Resource"),
      iconSrc: String(item.iconSrc || ""),
      category: String(item.category || ""),
      isContainer: Boolean(item.isContainer),
      parentId: item.parentId ? String(item.parentId) : null,
      width: clamp(Number(item.width) || CANVAS_CONTAINER.defaultWidth, CANVAS_CONTAINER.minWidth, 2400),
      height: clamp(Number(item.height) || CANVAS_CONTAINER.defaultHeight, CANVAS_CONTAINER.minHeight, 2400),
      x: Number(item.x) || 0,
      y: Number(item.y) || 0
    }))
    .filter((item) => item.iconSrc);

  const validItemIds = new Set(sanitizedItems.map((item) => item.id));
  const pattern = /^resource\s+(\d+)$/i;
  let maxResourceIndex = 0;
  sanitizedItems.forEach((item) => {
    const match = pattern.exec(String(item.name || "").trim());
    if (match) {
      maxResourceIndex = Math.max(maxResourceIndex, Number(match[1]) || 0);
    }
  });

  return {
    ...project,
    cloud,
    name: `${prefix}${suffix}`,
    lastSaved: Number(project.lastSaved) || Date.now(),
    canvasView: {
      x: Number(incomingView.x) || 0,
      y: Number(incomingView.y) || 0,
      zoom: clamp(Number(incomingView.zoom) || 1, CANVAS_ZOOM.min, CANVAS_ZOOM.max)
    },
    canvasItems: sanitizedItems,
    canvasConnections: incomingConnections
      .map((connection) => {
        const incomingName = String(connection.name || "").trim();
        const isLegacyArrowName = incomingName.includes("→");
        const hasExplicitName = incomingName.length > 0 && !isLegacyArrowName;
        const name = hasExplicitName ? incomingName : `Resource ${++maxResourceIndex}`;
        return {
          id: String(connection.id || `conn-${Date.now()}`),
          name,
          fromId: String(connection.fromId || ""),
          toId: String(connection.toId || ""),
          direction: connection.direction === "bi" ? "bi" : "one-way",
          sourceAnchor: ["top", "right", "bottom", "left"].includes(connection.sourceAnchor) ? connection.sourceAnchor : "right",
          targetAnchor: ["top", "right", "bottom", "left"].includes(connection.targetAnchor) ? connection.targetAnchor : "left"
        };
      })
      .filter((connection) => connection.fromId && connection.toId && validItemIds.has(connection.fromId) && validItemIds.has(connection.toId))
  };
}

function saveCurrentProject() {
  if (state.currentProject) {
    const projectId = state.currentProject.id;
    const idx = state.projects.findIndex((p) => p.id === projectId);
    if (idx !== -1) {
      state.projects[idx] = state.currentProject;
    }
  }
}

async function loadCurrentProject(projectId) {
  try {
    const response = await fetch(`/api/project/${encodeURIComponent(projectId)}`, { cache: "no-store" });
    if (!response.ok) {
      return false;
    }

    const payload = await response.json();
    const project = payload?.project && typeof payload.project === "object" ? payload.project : {};
    const canvasState = payload?.canvasState && typeof payload.canvasState === "object" ? payload.canvasState : {};

    const normalized = sanitizeProject({
      ...project,
      canvasView: canvasState.canvasView,
      canvasItems: canvasState.canvasItems,
      canvasConnections: canvasState.canvasConnections
    });

    if (!normalized) {
      return false;
    }

    state.currentProject = { ...normalized };
    return true;
  } catch {
    return false;
  }
}

// ===== Catalog Loading =====
async function loadCatalogForCloud(cloudName) {
  if (Object.hasOwn(cloudCatalogs, cloudName)) {
    return;
  }

  try {
    const response = await fetch(`./catalogs/${cloudName.toLowerCase()}.json`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Failed to load ${cloudName} catalog: ${response.status}`);
    }

    cloudCatalogs[cloudName] = await response.json();
  } catch {
    cloudCatalogs[cloudName] = null;
  }
}

// ===== Resource Rendering =====
function updatePropertyPanel(resourceName) {
  if (!resourceName) {
    selectedResourceNameEl.textContent = "None selected";
    propertyContentEl.textContent = "Select a resource or connection to edit properties.";
    return;
  }

  selectedResourceNameEl.textContent = resourceName;
  propertyContentEl.innerHTML = [
    "Properties",
    "- Name",
    "- Region",
    "- SKU / Tier",
    "- Tags"
  ].join("<br />");
}

function updatePropertyPanelForSelection() {
  const selectedConnection = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId) || null;
  if (selectedConnection) {
    const fromItem = getItemById(selectedConnection.fromId);
    const toItem = getItemById(selectedConnection.toId);
    selectedResourceNameEl.textContent = selectedConnection.name || "Unnamed Connection";
    
    const connectableItems = state.canvasItems.filter((item) => isConnectableItem(item));
    const fromOptions = connectableItems
      .map((item) => `<option value="${item.id}" ${item.id === selectedConnection.fromId ? "selected" : ""}>${item.name}</option>`)
      .join("");
    const toOptions = connectableItems
      .map((item) => `<option value="${item.id}" ${item.id === selectedConnection.toId ? "selected" : ""}>${item.name}</option>`)
      .join("");
    
    propertyContentEl.innerHTML = [
      "<div class=\"property-form\">",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">Name</span>",
      `<input class=\"property-input\" type=\"text\" value=\"${(selectedConnection.name || "").replace(/\"/g, "&quot;")}\" data-connection-field=\"name\" maxlength=\"80\" />`,
      "</label>",
      "<div class=\"property-row\"><span class=\"property-label\">Type</span><span class=\"property-value\">Connection</span></div>",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">From</span>",
      `<select class=\"property-input\" data-connection-field=\"fromId\"><option value=\"\">Select source...</option>${fromOptions}</select>`,
      "</label>",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">To</span>",
      `<select class=\"property-input\" data-connection-field=\"toId\"><option value=\"\">Select target...</option>${toOptions}</select>`,
      "</label>",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">Direction</span>",
      `<select class=\"property-input\" data-connection-field=\"direction\"><option value=\"one-way\" ${selectedConnection.direction === "one-way" ? "selected" : ""}>One-way</option><option value=\"bi\" ${selectedConnection.direction === "bi" ? "selected" : ""}>Bi-directional</option></select>`,
      "</label>",
      "<div class=\"property-actions\">",
      `<button class=\"btn btn--sm btn--primary\" type=\"button\" data-property-action=\"save\">Save</button>`,
      `<button class=\"btn btn--sm btn--danger\" type=\"button\" data-connection-action=\"remove\">Remove</button>`,
      "</div>"
    ].join("");
    return;
  }

  const selectedItem = getItemById(state.selectedResource);
  if (selectedItem) {
    selectedResourceNameEl.textContent = selectedItem.name;
    propertyContentEl.innerHTML = [
      "<div class=\"property-form\">",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">Name</span>",
      `<input class=\"property-input\" type=\"text\" value=\"${selectedItem.name.replace(/\"/g, "&quot;")}\" data-resource-field=\"name\" maxlength=\"80\" />`,
      "</label>",
      `<div class=\"property-row\"><span class=\"property-label\">Type</span><span class=\"property-value\">${selectedItem.resourceType || "Resource"}</span></div>`,
      `<div class=\"property-row\"><span class=\"property-label\">Category</span><span class=\"property-value\">${selectedItem.category || "N/A"}</span></div>`,
      `<div class=\"property-row\"><span class=\"property-label\">Position</span><span class=\"property-value\">(${selectedItem.x}, ${selectedItem.y})</span></div>`,
      `<div class=\"property-row\"><span class=\"property-label\">Connections</span><span class=\"property-value\">${state.canvasConnections.filter((connection) => connection.fromId === selectedItem.id || connection.toId === selectedItem.id).length}</span></div>`,
      "<div class=\"property-actions\">",
      `<button class=\"btn btn--sm btn--primary\" type=\"button\" data-property-action=\"save\">Save</button>`,
      "<button class=\"btn btn--sm btn--danger\" type=\"button\" data-resource-action=\"remove\">Remove</button>",
      "</div>",
      "</div>"
    ].join("");
    return;
  }

  selectedResourceNameEl.textContent = "None selected";
  propertyContentEl.textContent = "Select a resource or connection to edit properties.";
}

function createResourceRow(category, resource, iconRoot) {
  const row = document.createElement("button");
  row.className = "resource-row";
  row.type = "button";
  row.draggable = true;

  const icon = document.createElement("img");
  icon.className = "resource-icon";
  icon.alt = `${resource.name} icon`;
  const iconSrc = encodeURI(`${iconRoot}/${category}/${resource.icon}`);
  icon.src = iconSrc;
  icon.loading = "lazy";

  const name = document.createElement("span");
  name.className = "resource-name";
  name.textContent = resource.name;

  row.appendChild(icon);
  row.appendChild(name);

  row.addEventListener("dragstart", (event) => {
    const payload = {
      name: resource.name,
      resourceType: resource.name,
      iconSrc,
      category
    };
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/json", JSON.stringify(payload));
    event.dataTransfer.setData("text/plain", resource.name);
  });

  row.addEventListener("click", () => {
    // Resources in left panel shouldn't show properties - only canvas items should
    // Just highlight the row for visual feedback, but don't update property panel
    updateTimestamp();
  });

  return row;
}

function renderResources() {
  resourceListEl.innerHTML = "";

  if (!state.currentProject) {
    return;
  }

  const cloudCatalog = cloudCatalogs[state.currentProject.cloud];

  if (!cloudCatalog) {
    const empty = document.createElement("div");
    empty.className = "resource-empty";
    empty.textContent = `${state.currentProject.cloud} resource catalog is not configured yet.`;
    resourceListEl.appendChild(empty);
    state.selectedResource = null;
    updatePropertyPanel(null);
    return;
  }

  const categories = Object.keys(cloudCatalog).sort((first, second) => first.localeCompare(second));
  const iconRoot = getCloudIconRoot(state.currentProject.cloud);
  const search = state.searchTerm.trim().toLowerCase();
  let hasVisibleRows = false;

  categories.forEach((category) => {
    const resources = [...cloudCatalog[category]].sort((first, second) => first.name.localeCompare(second.name));
    const filtered = search
      ? resources.filter((resource) => resource.name.toLowerCase().includes(search))
      : resources;

    if (!filtered.length) {
      return;
    }

    hasVisibleRows = true;

    const heading = document.createElement("h4");
    heading.className = "resource-group-title";
    heading.innerHTML = `<span class="resource-group-toggle">▼</span><span>${titleCase(category)}</span>`;
    
    const groupBody = document.createElement("div");
    groupBody.className = "resource-group-body";

    // Add click handler for collapsible functionality
    heading.addEventListener("click", () => {
      const isCollapsed = heading.classList.toggle("collapsed");
      groupBody.classList.toggle("collapsed", isCollapsed);
    });

    filtered.forEach((resource) => {
      groupBody.appendChild(createResourceRow(category, resource, iconRoot));
    });

    resourceListEl.appendChild(heading);
    resourceListEl.appendChild(groupBody);
  });

  if (!hasVisibleRows) {
    const empty = document.createElement("div");
    empty.className = "resource-empty";
    empty.textContent = "No resources match your search.";
    resourceListEl.appendChild(empty);
  }
}

function updateTimestamp() {
  if (state.currentProject) {
    state.currentProject.lastSaved = Date.now();
    saveCurrentProject();
    projectTimestamp.textContent = `Last saved: ${formatTimestamp(state.currentProject.lastSaved)}`;
  }
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

function toWorldPoint(clientX, clientY) {
  const rect = canvasViewportEl.getBoundingClientRect();
  const screenX = clientX - rect.left;
  const screenY = clientY - rect.top;

  return {
    x: (screenX - state.canvasView.x) / state.canvasView.zoom,
    y: (screenY - state.canvasView.y) / state.canvasView.zoom
  };
}

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

function removeCanvasItemTree(itemId) {
  const directChildren = getChildrenByParentId(itemId);
  directChildren.forEach((child) => removeCanvasItemTree(child.id));

  state.canvasItems = state.canvasItems.filter((item) => item.id !== itemId);
  state.canvasConnections = state.canvasConnections.filter((connection) => connection.fromId !== itemId && connection.toId !== itemId);

  if (state.selectedResource === itemId) {
    state.selectedResource = null;
    updatePropertyPanel(null);
  }

  if (state.edgeDraft.sourceId === itemId) {
    state.edgeDraft.active = false;
    state.edgeDraft.sourceId = null;
  }

  if (state.selectedConnectionId) {
    const exists = state.canvasConnections.some((connection) => connection.id === state.selectedConnectionId);
    if (!exists) {
      state.selectedConnectionId = null;
    }
  }
}

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

function toWorldFromScreenPoint(screenX, screenY) {
  return {
    x: (screenX - state.canvasView.x) / state.canvasView.zoom,
    y: (screenY - state.canvasView.y) / state.canvasView.zoom
  };
}

function getAnchorWorldPoint(itemId, anchor = "right") {
  const screen = getAnchorScreenPoint(itemId, anchor);
  if (!screen) {
    return null;
  }
  return toWorldFromScreenPoint(screen.x, screen.y);
}

function findClosestByClass(element, className) {
  let cursor = element;
  while (cursor && cursor !== document.body) {
    if (cursor.classList && cursor.classList.contains(className)) {
      return cursor;
    }
    cursor = cursor.parentNode;
  }
  return null;
}

function findInEventPath(event, className) {
  if (!event.composedPath) {
    return findClosestByClass(event.target, className);
  }

  const path = event.composedPath();
  return path.find((node) => node?.classList?.contains?.(className)) || null;
}

function getNearestAnchorFromClient(itemId, clientX, clientY) {
  if (!canvasViewportEl) {
    return "left";
  }

  const rect = getNodeScreenRect(itemId);
  if (!rect) {
    return "left";
  }

  const viewportRect = canvasViewportEl.getBoundingClientRect();
  const sx = clientX - viewportRect.left;
  const sy = clientY - viewportRect.top;

  const distances = [
    { anchor: "top", distance: Math.abs(sy - rect.top) },
    { anchor: "right", distance: Math.abs(sx - (rect.left + rect.width)) },
    { anchor: "bottom", distance: Math.abs(sy - (rect.top + rect.height)) },
    { anchor: "left", distance: Math.abs(sx - rect.left) }
  ];

  distances.sort((first, second) => first.distance - second.distance);
  return distances[0].anchor;
}

function getConnectionPath(connection) {
  const fromWorld = getAnchorWorldPoint(connection.fromId, connection.sourceAnchor || "right");
  const toWorld = getAnchorWorldPoint(connection.toId, connection.targetAnchor || "left");
  if (!fromWorld || !toWorld) {
    return null;
  }

  return {
    path: `M ${fromWorld.x} ${fromWorld.y} L ${toWorld.x} ${toWorld.y}`,
    midX: (fromWorld.x + toWorld.x) / 2,
    midY: (fromWorld.y + toWorld.y) / 2
  };
}

function clearEdgeDraft() {
  state.edgeDraft.active = false;
  state.edgeDraft.sourceId = null;
  state.edgeDraft.sourceAnchor = "right";
  state.edgeDraft.targetId = null;
  state.edgeDraft.targetAnchor = "left";
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
  markerEnd.setAttribute("markerWidth", "10");
  markerEnd.setAttribute("markerHeight", "10");
  markerEnd.setAttribute("markerUnits", "userSpaceOnUse");
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
    const sourceWorld = getAnchorWorldPoint(state.edgeDraft.sourceId, state.edgeDraft.sourceAnchor);
    if (sourceWorld) {
      const targetWorld = toWorldPoint(state.edgeDraft.endClientX, state.edgeDraft.endClientY);
      const draftPath = document.createElementNS(namespace, "path");
      draftPath.classList.add("canvas-edge");
      draftPath.setAttribute("d", `M ${sourceWorld.x} ${sourceWorld.y} L ${targetWorld.x} ${targetWorld.y}`);
      draftPath.setAttribute("marker-end", "url(#edge-arrow-end)");
      draftPath.setAttribute("stroke-dasharray", "4 4");
      canvasEdgesEl.appendChild(draftPath);
    }
  }
}

function upsertConnection(fromId, toId, direction, sourceAnchor = "right", targetAnchor = "left") {
  const existing = state.canvasConnections.find((connection) => connection.fromId === fromId && connection.toId === toId);
  if (existing) {
    existing.direction = direction;
    existing.sourceAnchor = sourceAnchor;
    existing.targetAnchor = targetAnchor;
    state.selectedConnectionId = existing.id;
    return;
  }

  const newName = getNextResourceDefaultName();

  const newConnection = {
    id: `conn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: newName,
    fromId,
    toId,
    direction,
    sourceAnchor,
    targetAnchor
  };

  state.canvasConnections.push(newConnection);
  state.selectedConnectionId = newConnection.id;
  updateCanvasStatus();
}

function updateCanvasNodeSelection() {
  if (!canvasLayerEl) {
    return;
  }

  canvasLayerEl.querySelectorAll(".canvas-node").forEach((nodeEl) => {
    const isSelected = nodeEl.dataset.itemId === state.selectedResource;
    const isReceptor = state.edgeDraft.active && nodeEl.dataset.itemId === state.edgeDraft.targetId;
    nodeEl.classList.toggle("is-selected", isSelected);
    nodeEl.classList.toggle("is-receptor-target", Boolean(isReceptor));

    nodeEl.querySelectorAll(".canvas-connect-handle").forEach((handleEl) => {
      const isActiveReceptor = isReceptor && handleEl.dataset.anchor === state.edgeDraft.targetAnchor;
      handleEl.classList.toggle("is-receptor-active", Boolean(isActiveReceptor));
    });
  });
}

function updateCanvasStatus() {
  if (canvasStatusEl) {
    const resourceCount = state.canvasItems.length;
    const connectionCount = state.canvasConnections.length;
    canvasStatusEl.textContent = `${resourceCount} resource${resourceCount !== 1 ? 's' : ''} · ${connectionCount} connection${connectionCount !== 1 ? 's' : ''}`;
  }
}

function renderCanvasItems() {
  if (!canvasLayerEl) {
    return;
  }

  canvasLayerEl.innerHTML = "";

  function renderNode(item, host) {
    const nodeEl = document.createElement("div");
    nodeEl.className = "canvas-node";
    if (item.isContainer) {
      nodeEl.classList.add("canvas-node--container");
      nodeEl.style.width = `${item.width || CANVAS_CONTAINER.defaultWidth}px`;
      nodeEl.style.height = `${item.height || CANVAS_CONTAINER.defaultHeight}px`;
    }
    nodeEl.dataset.itemId = item.id;
    nodeEl.style.transform = `translate(${item.x}px, ${item.y}px)`;
    const resourceType = item.resourceType || item.name;

    const iconEl = document.createElement("img");
    iconEl.src = item.iconSrc;
    iconEl.alt = `${resourceType} icon`;
    iconEl.draggable = false;

    const nameEl = document.createElement("span");
    nameEl.textContent = item.name;

    if (item.isContainer) {
      const headerEl = document.createElement("div");
      headerEl.className = "canvas-container-header";
      headerEl.appendChild(iconEl);
      headerEl.appendChild(nameEl);

      const removeButton = buildRemoveControl(item.id);
      headerEl.appendChild(removeButton);

      const bodyEl = document.createElement("div");
      bodyEl.className = "canvas-container-body";

      nodeEl.appendChild(headerEl);
      nodeEl.appendChild(bodyEl);

      const resizeHandle = document.createElement("div");
      resizeHandle.className = "canvas-resize-handle";
      resizeHandle.dataset.itemId = item.id;
      resizeHandle.title = "Resize container";
      nodeEl.appendChild(resizeHandle);

      getChildrenByParentId(item.id).forEach((child) => {
        renderNode(child, bodyEl);
      });
    } else {
      const nameInputEl = document.createElement("input");
      nameInputEl.className = "canvas-node-namebox";
      nameInputEl.type = "text";
      nameInputEl.value = item.name;
      nameInputEl.readOnly = true;
      nameInputEl.tabIndex = -1;

      const typeEl = document.createElement("span");
      typeEl.className = "canvas-node-type";
      typeEl.textContent = resourceType;

      nodeEl.appendChild(nameInputEl);
      nodeEl.appendChild(iconEl);
      nodeEl.appendChild(typeEl);
      nodeEl.appendChild(buildRemoveControl(item.id));
    }

    if (isConnectableItem(item)) {
      ["top", "right", "bottom", "left"].forEach((anchor) => {
        nodeEl.appendChild(buildConnectHandle(item.id, anchor));
      });
    }

    host.appendChild(nodeEl);
  }

  getChildrenByParentId(null).forEach((rootItem) => {
    renderNode(rootItem, canvasLayerEl);
  });

  updateCanvasNodeSelection();
  renderCanvasConnections();
}

function renderCanvasView() {
  if (!canvasLayerEl || !canvasGridEl) {
    return;
  }

  canvasLayerEl.style.transform = `translate(${state.canvasView.x}px, ${state.canvasView.y}px) scale(${state.canvasView.zoom})`;
  if (canvasEdgesEl) {
    canvasEdgesEl.style.transform = `translate(${state.canvasView.x}px, ${state.canvasView.y}px) scale(${state.canvasView.zoom})`;
  }

  const gridStep = CANVAS_ZOOM.grid * state.canvasView.zoom;
  const offsetX = ((state.canvasView.x % gridStep) + gridStep) % gridStep;
  const offsetY = ((state.canvasView.y % gridStep) + gridStep) % gridStep;
  canvasGridEl.style.backgroundSize = `${gridStep}px ${gridStep}px`;
  canvasGridEl.style.backgroundPosition = `${offsetX}px ${offsetY}px`;

  if (canvasZoomLabelEl) {
    canvasZoomLabelEl.textContent = `${Math.round(state.canvasView.zoom * 100)}%`;
  }

  renderCanvasConnections();
  updateCanvasStatus();
}

function updateCanvasStatus() {
  if (!canvasStatusEl) {
    return;
  }
  
  const resourceCount = state.canvasItems.length;
  const connectionCount = state.canvasConnections.length;
  canvasStatusEl.textContent = `${resourceCount} resource${resourceCount !== 1 ? 's' : ''} · ${connectionCount} connection${connectionCount !== 1 ? 's' : ''}`;
}

function selectCanvasItem(itemId) {
  const item = getItemById(itemId);
  state.selectedResource = item ? item.id : null;
  state.selectedConnectionId = null;
  updatePropertyPanelForSelection();
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
    iconSrc: resource.iconSrc,
    category: resource.category || "",
    isContainer: containerResource,
    parentId,
    width: containerResource ? CANVAS_CONTAINER.defaultWidth : undefined,
    height: containerResource ? CANVAS_CONTAINER.defaultHeight : undefined,
    x: snappedX,
    y: snappedY
  };

  state.canvasItems.push(newItem);
  renderCanvasItems();
  selectCanvasItem(newItem.id);
  persistCanvasLocal();
  updateCanvasStatus();
}

function initializeCanvasInteractions() {
  if (!canvasViewportEl || !canvasLayerEl || !canvasGridEl) {
    return;
  }

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
    } catch {
      // Ignore malformed drag payloads.
    }
  });

  canvasViewportEl.addEventListener("wheel", (event) => {
    event.preventDefault();
    const direction = event.deltaY < 0 ? 1 : -1;
    adjustCanvasZoom(direction, event.clientX, event.clientY);
  }, { passive: false });

  canvasViewportEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0 && event.button !== 1) {
      return;
    }

    if (event.target.closest(".canvas-node")) {
      return;
    }

    event.preventDefault();
    state.selectedConnectionId = null;
    state.selectedResource = null;
    updatePropertyPanelForSelection();
    updateCanvasNodeSelection();
    renderCanvasConnections();
    canvasInteraction.isPanning = true;
    canvasViewportEl.classList.add("is-panning");
    canvasInteraction.panOriginX = event.clientX;
    canvasInteraction.panOriginY = event.clientY;
    canvasInteraction.viewOriginX = state.canvasView.x;
    canvasInteraction.viewOriginY = state.canvasView.y;
  });

  canvasLayerEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0) {
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

  canvasLayerEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0) {
      return;
    }

    if (state.edgeDraft.active) {
      return;
    }

    if (event.target.closest(".canvas-connect-handle")) {
      return;
    }

    if (event.target.closest(".canvas-node-remove")) {
      return;
    }

    if (event.target.closest(".canvas-resize-handle")) {
      return;
    }

    const nodeEl = event.target.closest(".canvas-node");
    if (!nodeEl) {
      return;
    }

    const item = getItemById(nodeEl.dataset.itemId);
    if (!item) {
      return;
    }

    if (item.isContainer && !event.target.closest(".canvas-container-header")) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    selectCanvasItem(item.id);
    canvasInteraction.draggingItemId = item.id;
    canvasInteraction.dragOriginX = event.clientX;
    canvasInteraction.dragOriginY = event.clientY;
    canvasInteraction.itemOriginX = item.x;
    canvasInteraction.itemOriginY = item.y;
  });

  canvasLayerEl.addEventListener("click", (event) => {
    const nodeEl = event.target.closest(".canvas-node");
    if (!nodeEl) {
      return;
    }

    selectCanvasItem(nodeEl.dataset.itemId);
  });

  canvasEdgesEl?.addEventListener("click", (event) => {
    const edgeEl = findInEventPath(event, "canvas-edge");
    if (!edgeEl || !edgeEl.dataset.connectionId) {
      state.selectedConnectionId = null;
      updatePropertyPanelForSelection();
      renderCanvasConnections();
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    state.selectedConnectionId = edgeEl.dataset.connectionId;
    state.selectedResource = null;
    updatePropertyPanelForSelection();
    updateCanvasNodeSelection();
    renderCanvasConnections();
  });

  canvasEdgesEl?.addEventListener("mousedown", (event) => {
    if (findInEventPath(event, "canvas-edge")) {
      event.stopPropagation();
    }
  });

  window.addEventListener("mousemove", (event) => {
    if (state.edgeDraft.active) {
      state.edgeDraft.endClientX = event.clientX;
      state.edgeDraft.endClientY = event.clientY;

      const pointerTarget = document.elementFromPoint(event.clientX, event.clientY);
      const receptorHandle = pointerTarget?.closest(".canvas-connect-handle");
      const receptorNode = pointerTarget?.closest(".canvas-node");
      const sourceId = state.edgeDraft.sourceId;

      if (receptorHandle) {
        const targetId = receptorHandle.dataset.itemId;
        const targetItem = getItemById(targetId);
        if (targetId && targetId !== sourceId && isConnectableItem(targetItem)) {
          state.edgeDraft.targetId = targetId;
          state.edgeDraft.targetAnchor = receptorHandle.dataset.anchor || "left";
        } else {
          state.edgeDraft.targetId = null;
          state.edgeDraft.targetAnchor = "left";
        }
      } else if (receptorNode) {
        const targetId = receptorNode.dataset.itemId;
        const targetItem = getItemById(targetId);
        if (targetId && targetId !== sourceId && isConnectableItem(targetItem)) {
          state.edgeDraft.targetId = targetId;
          state.edgeDraft.targetAnchor = getNearestAnchorFromClient(targetId, event.clientX, event.clientY);
        } else {
          state.edgeDraft.targetId = null;
          state.edgeDraft.targetAnchor = "left";
        }
      } else {
        state.edgeDraft.targetId = null;
        state.edgeDraft.targetAnchor = "left";
      }

      updateCanvasNodeSelection();
      renderCanvasConnections();
      return;
    }

    if (canvasInteraction.resizingItemId) {
      const item = getItemById(canvasInteraction.resizingItemId);
      if (!item || !item.isContainer) {
        return;
      }

      const dx = (event.clientX - canvasInteraction.resizeOriginX) / state.canvasView.zoom;
      const dy = (event.clientY - canvasInteraction.resizeOriginY) / state.canvasView.zoom;
      item.width = clamp(canvasInteraction.widthOrigin + dx, CANVAS_CONTAINER.minWidth, 2400);
      item.height = clamp(canvasInteraction.heightOrigin + dy, CANVAS_CONTAINER.minHeight, 2400);
      renderCanvasItems();
      return;
    }

    if (canvasInteraction.isPanning) {
      const dx = event.clientX - canvasInteraction.panOriginX;
      const dy = event.clientY - canvasInteraction.panOriginY;
      state.canvasView.x = canvasInteraction.viewOriginX + dx;
      state.canvasView.y = canvasInteraction.viewOriginY + dy;
      renderCanvasView();
      return;
    }

    if (canvasInteraction.draggingItemId) {
      const item = getItemById(canvasInteraction.draggingItemId);
      if (!item) {
        return;
      }

      const dx = (event.clientX - canvasInteraction.dragOriginX) / state.canvasView.zoom;
      const dy = (event.clientY - canvasInteraction.dragOriginY) / state.canvasView.zoom;
      item.x = Math.round((canvasInteraction.itemOriginX + dx) / 10) * 10;
      item.y = Math.round((canvasInteraction.itemOriginY + dy) / 10) * 10;
      renderCanvasItems();
    }
  });

  window.addEventListener("mouseup", () => {
    if (state.edgeDraft.active && state.edgeDraft.sourceId) {
      const sourceId = state.edgeDraft.sourceId;

      if (state.edgeDraft.targetId && state.edgeDraft.targetId !== sourceId) {
        const targetItem = getItemById(state.edgeDraft.targetId);
        if (isConnectableItem(targetItem)) {
          upsertConnection(sourceId, state.edgeDraft.targetId, "one-way", state.edgeDraft.sourceAnchor, state.edgeDraft.targetAnchor);
          persistCanvasLocal();
        }
      }

      clearEdgeDraft();
      updateCanvasNodeSelection();
      renderCanvasConnections();
    }

    if (canvasInteraction.isPanning) {
      canvasInteraction.isPanning = false;
      canvasViewportEl.classList.remove("is-panning");
      persistCanvasLocal();
    }

    if (canvasInteraction.draggingItemId) {
      const item = getItemById(canvasInteraction.draggingItemId);
      if (item) {
        const world = getItemWorldPosition(item.id);
        const bodyX = world.x + 60;
        const bodyY = world.y + 24;
        const targetContainer = getContainerAtWorldPoint(bodyX, bodyY, item.id);
        const nextParentId = targetContainer ? targetContainer.id : null;

        if (item.parentId !== nextParentId) {
          moveItemToParent(item, nextParentId);
          renderCanvasItems();
        }
      }

      canvasInteraction.draggingItemId = null;
      persistCanvasLocal();
    }

    if (canvasInteraction.resizingItemId) {
      canvasInteraction.resizingItemId = null;
      persistCanvasLocal();
    }
  });

  canvasLayerEl.addEventListener("mousedown", (event) => {
    if (event.target.closest(".canvas-node-remove")) {
      return;
    }

    const resizeEl = event.target.closest(".canvas-resize-handle");
    if (!resizeEl) {
      return;
    }

    const item = getItemById(resizeEl.dataset.itemId);
    if (!item || !item.isContainer) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    canvasInteraction.resizingItemId = item.id;
    canvasInteraction.resizeOriginX = event.clientX;
    canvasInteraction.resizeOriginY = event.clientY;
    canvasInteraction.widthOrigin = Number(item.width) || CANVAS_CONTAINER.defaultWidth;
    canvasInteraction.heightOrigin = Number(item.height) || CANVAS_CONTAINER.defaultHeight;
  });

  canvasLayerEl.addEventListener("click", (event) => {
    const removeEl = event.target.closest(".canvas-node-remove");
    if (!removeEl) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    removeCanvasItemTree(removeEl.dataset.itemId);
    renderCanvasItems();
    updateCanvasStatus();
    persistCanvasLocal();
  });

  canvasZoomInBtn?.addEventListener("click", () => adjustCanvasZoom(1));
  canvasZoomOutBtn?.addEventListener("click", () => adjustCanvasZoom(-1));
  canvasResetViewBtn?.addEventListener("click", () => {
    state.canvasView = { x: 0, y: 0, zoom: 1 };
    renderCanvasView();
    persistCanvasLocal();
  });
}

function setSaveStatus(message, isError = false) {
  if (!projectSaveStatus) {
    return;
  }

  projectSaveStatus.textContent = message;
  projectSaveStatus.style.color = isError ? "#b91c1c" : "";
}

function buildProjectSnapshot() {
  return {
    project: {
      id: state.currentProject.id,
      name: state.currentProject.name,
      cloud: state.currentProject.cloud,
      lastSaved: state.currentProject.lastSaved
    },
    canvasState: {
      leftWidth: state.leftWidth,
      rightWidth: state.rightWidth,
      bottomHeight: state.bottomHeight,
      bottomRightWidth: state.bottomRightWidth,
      selectedResource: state.selectedResource,
      searchTerm: state.searchTerm,
      canvasView: {
        x: state.canvasView.x,
        y: state.canvasView.y,
        zoom: state.canvasView.zoom
      },
      canvasItems: state.canvasItems.map((item) => ({ ...item })),
      canvasConnections: state.canvasConnections.map((connection) => ({ ...connection }))
    }
  };
}

async function saveProjectFiles(options = {}) {
  if (!state.currentProject) {
    return;
  }

  const { silent = false } = options;
  const snapshot = buildProjectSnapshot();

  try {
    const response = await fetch("/api/project/save", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(snapshot)
    });

    if (!response.ok) {
      throw new Error("Unable to write project files");
    }

    if (!silent) {
      setSaveStatus(`Saved at ${new Date().toLocaleTimeString()}`);
    }
  } catch {
    if (!silent) {
      setSaveStatus("Save failed", true);
    }
    throw new Error("Save failed");
  }
}

// ===== Sizing & Splitters =====
function applySizes() {
  appEl.style.setProperty("--left-width", `${state.leftWidth}px`);
  appEl.style.setProperty("--right-width", `${state.rightWidth}px`);
  appEl.style.setProperty("--bottom-height", `${state.bottomHeight}px`);
  appEl.style.setProperty("--bottom-right-width", `${state.bottomRightWidth}px`);

  if (statusLeftWidthEl) {
    statusLeftWidthEl.textContent = `${Math.round(state.leftWidth)}px`;
  }

  if (statusRightWidthEl) {
    statusRightWidthEl.textContent = `${Math.round(state.rightWidth)}px`;
  }
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
}

function startDrag(getValue) {
  function onMove(event) {
    getValue(event);
    applySizes();
  }

  function onUp() {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
  }

  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

document.querySelector('[data-splitter="left"]')?.addEventListener("mousedown", () => {
  startDrag((event) => {
    state.leftWidth = clamp(event.clientX, constraints.leftMin, constraints.leftMax);
  });
});

document.querySelector('[data-splitter="right"]')?.addEventListener("mousedown", () => {
  startDrag((event) => {
    const width = window.innerWidth - event.clientX;
    state.rightWidth = clamp(width, constraints.rightMin, constraints.rightMax);
  });
});

// ===== Tab Behavior =====
tabGroups.forEach((group) => {
  group.tabs.forEach((tab) => {
    function activateTab() {
      const name = tab.dataset.tab;

      group.tabs.forEach((item) => {
        const active = item === tab;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
        item.setAttribute("tabindex", active ? "0" : "-1");
      });

      group.panels.forEach((panelEl, panelName) => {
        const hidden = panelName !== name;
        panelEl.classList.toggle("is-hidden", hidden);
        panelEl.toggleAttribute("hidden", hidden);
      });
    }

    tab.addEventListener("click", activateTab);
    tab.addEventListener("focus", activateTab);
    tab.addEventListener("keydown", (event) => {
      if (event.key === "Enter" || event.key === " ") {
        event.preventDefault();
        activateTab();
      }

      if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
        event.preventDefault();
        const currentIndex = group.tabs.indexOf(tab);
        const direction = event.key === "ArrowRight" ? 1 : -1;
        const nextIndex = (currentIndex + direction + group.tabs.length) % group.tabs.length;
        group.tabs[nextIndex].focus();
      }
    });
  });
});

function appendChatMessage(message) {
  if (!chatHistoryEl) {
    return;
  }

  const messageEl = document.createElement("div");
  messageEl.className = "chat-message chat-message--user";
  messageEl.textContent = message;
  chatHistoryEl.appendChild(messageEl);
  chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
}

function sendChatMessage() {
  if (!chatInputEl) {
    return;
  }

  const message = chatInputEl.value.trim();
  if (!message) {
    return;
  }

  appendChatMessage(message);
  chatInputEl.value = "";
}

chatSendBtn?.addEventListener("click", sendChatMessage);
chatInputEl?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
});

// ===== Project Name Editing =====
projectNameDisplay?.addEventListener("blur", () => {
  if (!state.currentProject) {
    return;
  }

  const rawSuffix = projectNameDisplay.textContent.trim();
  const { prefix, suffix } = splitProjectName(state.currentProject.cloud, rawSuffix);

  if (suffix) {
    state.currentProject.name = `${prefix}${suffix}`;
    saveCurrentProject();
    updateTimestamp();
  } else {
    renderProjectName();
  }
});

projectNameDisplay?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    projectNameDisplay.blur();
  }
});

// ===== Event Listeners =====
btnBackProjects.addEventListener("click", async () => {
  try {
    await saveProjectFiles();
  } catch {
    // Continue navigation even if file save fails.
  }
  window.location.href = "./landing.html";
});

btnProjectSave?.addEventListener("click", async () => {
  updateTimestamp();
  await saveProjectFiles();
});

btnProjectSettings?.addEventListener("click", () => {
  if (!state.currentProject) {
    return;
  }

  const params = new URLSearchParams();
  params.set("projectId", state.currentProject.id);
  window.location.href = `./project-settings.html?${params.toString()}`;
});

searchInput?.addEventListener("input", () => {
  state.searchTerm = searchInput.value;
  renderResources();
});

propertyContentEl?.addEventListener("click", (event) => {
  // Handle Save button click
  const saveBtn = event.target.closest("[data-property-action='save']");
  if (saveBtn) {
    persistCanvasLocal();
    renderCanvasItems();
    renderCanvasConnections();
    
    // Visual feedback
    const originalText = saveBtn.textContent;
    saveBtn.textContent = "Saved!";
    saveBtn.disabled = true;
    setTimeout(() => {
      saveBtn.textContent = originalText;
      saveBtn.disabled = false;
    }, 1500);
    return;
  }

  // Handle connection action buttons
  const actionEl = event.target.closest("[data-connection-action]");
  if (!actionEl || !state.selectedConnectionId) {
    const resourceActionEl = event.target.closest("[data-resource-action]");
    if (!resourceActionEl || !state.selectedResource) {
      return;
    }

    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    if (resourceActionEl.dataset.resourceAction === "remove") {
      removeCanvasItemTree(selectedItem.id);
      state.selectedResource = null;
      updatePropertyPanelForSelection();
      renderCanvasItems();
      updateCanvasStatus();
      persistCanvasLocal();
    }
    return;
  }

  const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
  if (!selected) {
    return;
  }

  const action = actionEl.dataset.connectionAction;

  if (action === "reverse") {
    const fromId = selected.fromId;
    const sourceAnchor = selected.sourceAnchor;
    selected.fromId = selected.toId;
    selected.toId = fromId;
    selected.sourceAnchor = selected.targetAnchor;
    selected.targetAnchor = sourceAnchor;
  }

  if (action === "toggle-direction") {
    selected.direction = selected.direction === "bi" ? "one-way" : "bi";
  }

  if (action === "remove") {
    state.canvasConnections = state.canvasConnections.filter((connection) => connection.id !== state.selectedConnectionId);
    state.selectedConnectionId = null;
    updateCanvasStatus();
  }

  updatePropertyPanelForSelection();
  renderCanvasConnections();
  persistCanvasLocal();
});

propertyContentEl?.addEventListener("change", (event) => {
  const target = event.target;

  if (target.matches("[data-connection-field='name']") && state.selectedConnectionId) {
    const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
    if (!selected) {
      return;
    }

    const nextName = String(target.value || "").trim();
    if (!nextName) {
      updatePropertyPanelForSelection();
      return;
    }

    selected.name = nextName;
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if ((target.matches("[data-connection-field='fromId']") || target.matches("[data-connection-field='toId']")) && state.selectedConnectionId) {
    const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
    if (!selected) {
      return;
    }

    const newValue = target.value;
    if (!newValue) {
      return; // Don't allow empty selection
    }

    const fieldName = target.dataset.connectionField;
    const oppositeField = fieldName === "fromId" ? "toId" : "fromId";
    const oppositeValue = selected[oppositeField];

    // Validation: prevent same resource in both From and To
    if (newValue === oppositeValue) {
      updatePropertyPanelForSelection(); // Revert the change by re-rendering
      return;
    }

    // Update the connection field
    selected[fieldName] = newValue;

    // Update corresponding anchor (default to right for From, left for To)
    if (fieldName === "fromId") {
      selected.sourceAnchor = "right";
    } else {
      selected.targetAnchor = "left";
    }

    updatePropertyPanelForSelection();
    renderCanvasConnections();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-connection-field='direction']") && state.selectedConnectionId) {
    const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
    if (!selected) {
      return;
    }

    selected.direction = target.value === "bi" ? "bi" : "one-way";
    updatePropertyPanelForSelection();
    renderCanvasConnections();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='name']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const nextName = String(target.value || "").trim();
    if (!nextName) {
      updatePropertyPanelForSelection();
      return;
    }

    selectedItem.name = nextName;

    updatePropertyPanelForSelection();
    renderCanvasItems();
    renderCanvasConnections();
    persistCanvasLocal();
  }
});

// ===== Initialization =====
async function initialize() {
  // Get projectId from URL params
  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("projectId");

  if (!projectId) {
    console.error("No project ID provided");
    window.location.href = "./landing.html";
    return;
  }

  // Load this specific project from backend files
  if (!await loadCurrentProject(projectId)) {
    console.error("Project not found");
    window.location.href = "./landing.html";
    return;
  }

  const { prefix, suffix } = splitProjectName(state.currentProject.cloud, state.currentProject.name);
  state.currentProject.name = `${prefix}${suffix}`;
  state.canvasView = {
    x: Number(state.currentProject.canvasView?.x) || 0,
    y: Number(state.currentProject.canvasView?.y) || 0,
    zoom: clamp(Number(state.currentProject.canvasView?.zoom) || 1, CANVAS_ZOOM.min, CANVAS_ZOOM.max)
  };
  state.canvasItems = Array.isArray(state.currentProject.canvasItems)
    ? state.currentProject.canvasItems.map((item) => ({ ...item }))
    : [];
  state.canvasConnections = Array.isArray(state.currentProject.canvasConnections)
    ? state.currentProject.canvasConnections.map((connection) => ({ ...connection }))
    : [];
  saveCurrentProject();

  // Update UI with project info
  renderProjectName();
  updateTimestamp();

  // Initialize layout
  applySizes();
  initializeCanvasInteractions();
  renderCanvasItems();
  renderCanvasView();
  updateCanvasStatus();

  setSaveStatus("Autosave: every 60s");
  window.setInterval(async () => {
    try {
      updateTimestamp();
      await saveProjectFiles({ silent: true });
    } catch {
      // Keep autosave non-blocking.
    }
  }, AUTOSAVE_INTERVAL_MS);

  // Load catalog and render resources
  loadCatalogForCloud(state.currentProject.cloud).then(() => {
    renderResources();
  }).catch(() => {
    renderResources();
  });

  // Initialize properties panel
  updatePropertyPanel(null);
}

initialize().catch((error) => {
  console.error("Canvas initialization failed", error);
  if (projectNamePrefixDisplay) {
    projectNamePrefixDisplay.textContent = "Project unavailable";
  }
  if (projectNameDisplay) {
    projectNameDisplay.textContent = "";
  }
  if (resourceListEl) {
    resourceListEl.innerHTML = "";
    const errorRow = document.createElement("div");
    errorRow.className = "resource-empty";
    errorRow.textContent = "Unable to load this project. Return to Projects and reopen.";
    resourceListEl.appendChild(errorRow);
  }
});
