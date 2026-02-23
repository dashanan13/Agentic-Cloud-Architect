// ===== UI Element References =====
const btnBackProjects = document.getElementById("btn-back-projects");
const btnProjectSave = document.getElementById("btn-project-save");
const btnProjectSettings = document.getElementById("btn-project-settings");
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
const tabs = Array.from(document.querySelectorAll(".tab"));
const panels = {
  chat: document.getElementById("panel-chat"),
  terminal: document.getElementById("panel-terminal")
};

// ===== State =====
const cloudCatalogs = {};
const state = {
  projects: [],
  currentProject: null,
  leftWidth: 220,
  rightWidth: 260,
  bottomHeight: 160,
  bottomRightWidth: 240,
  selectedResource: null,
  searchTerm: "",
  canvasView: {
    x: 0,
    y: 0,
    zoom: 1
  },
  canvasItems: []
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
  leftMin: 220,
  leftMax: 480,
  rightMin: 260,
  rightMax: 500,
  bottomMin: 160,
  bottomMax: 380,
  bottomRightMin: 240,
  bottomRightMax: 520
};

const MAX_PROJECT_NAME_LENGTH = 27;
const AUTOSAVE_INTERVAL_MS = 60000;
const CANVAS_ZOOM = {
  min: 0.25,
  max: 2.5,
  step: 0.1,
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
    canvasItems: incomingItems
      .map((item) => ({
        id: String(item.id || `item-${Date.now()}`),
        name: String(item.name || "Resource"),
        iconSrc: String(item.iconSrc || ""),
        category: String(item.category || ""),
        isContainer: Boolean(item.isContainer),
        parentId: item.parentId ? String(item.parentId) : null,
        width: clamp(Number(item.width) || CANVAS_CONTAINER.defaultWidth, CANVAS_CONTAINER.minWidth, 2400),
        height: clamp(Number(item.height) || CANVAS_CONTAINER.defaultHeight, CANVAS_CONTAINER.minHeight, 2400),
        x: Number(item.x) || 0,
        y: Number(item.y) || 0
      }))
      .filter((item) => item.iconSrc)
  };
}

// ===== LocalStorage =====
function saveProjects() {
  localStorage.setItem("a3_projects", JSON.stringify(state.projects));
}

function loadProjects() {
  const stored = localStorage.getItem("a3_projects");
  state.projects = stored ? JSON.parse(stored) : [];
  state.projects = state.projects.map(sanitizeProject).filter(Boolean);
}

function saveCurrentProject() {
  if (state.currentProject) {
    const projectId = state.currentProject.id;
    const idx = state.projects.findIndex((p) => p.id === projectId);
    if (idx !== -1) {
      state.projects[idx] = state.currentProject;
      saveProjects();
    }
  }
}

function loadCurrentProject(projectId) {
  const project = state.projects.find((p) => p.id === projectId);
  if (project) {
    state.currentProject = { ...project };
    return true;
  }
  return false;
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
    propertyContentEl.textContent = "Select a resource from the left panel to preview properties.";
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
      iconSrc,
      category
    };
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/json", JSON.stringify(payload));
    event.dataTransfer.setData("text/plain", resource.name);
  });

  row.addEventListener("click", () => {
    state.selectedResource = resource.name;
    updatePropertyPanel(resource.name);
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
    heading.textContent = titleCase(category);
    resourceListEl.appendChild(heading);

    const groupBody = document.createElement("div");
    groupBody.className = "resource-group-body";

    filtered.forEach((resource) => {
      groupBody.appendChild(createResourceRow(category, resource, iconRoot));
    });

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

  if (state.selectedResource === itemId) {
    state.selectedResource = null;
    updatePropertyPanel(null);
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

function updateCanvasNodeSelection() {
  if (!canvasLayerEl) {
    return;
  }

  canvasLayerEl.querySelectorAll(".canvas-node").forEach((nodeEl) => {
    nodeEl.classList.toggle("is-selected", nodeEl.dataset.itemId === state.selectedResource);
  });
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

    const iconEl = document.createElement("img");
    iconEl.src = item.iconSrc;
    iconEl.alt = `${item.name} icon`;
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
      nodeEl.appendChild(iconEl);
      nodeEl.appendChild(nameEl);
      nodeEl.appendChild(buildRemoveControl(item.id));
    }

    host.appendChild(nodeEl);
  }

  getChildrenByParentId(null).forEach((rootItem) => {
    renderNode(rootItem, canvasLayerEl);
  });

  updateCanvasNodeSelection();
}

function renderCanvasView() {
  if (!canvasLayerEl || !canvasGridEl) {
    return;
  }

  canvasLayerEl.style.transform = `translate(${state.canvasView.x}px, ${state.canvasView.y}px) scale(${state.canvasView.zoom})`;

  const gridStep = CANVAS_ZOOM.grid * state.canvasView.zoom;
  const offsetX = ((state.canvasView.x % gridStep) + gridStep) % gridStep;
  const offsetY = ((state.canvasView.y % gridStep) + gridStep) % gridStep;
  canvasGridEl.style.backgroundSize = `${gridStep}px ${gridStep}px`;
  canvasGridEl.style.backgroundPosition = `${offsetX}px ${offsetY}px`;

  if (canvasZoomLabelEl) {
    canvasZoomLabelEl.textContent = `${Math.round(state.canvasView.zoom * 100)}%`;
  }
}

function selectCanvasItem(itemId) {
  const item = getItemById(itemId);
  state.selectedResource = item ? item.id : null;
  updatePropertyPanel(item ? item.name : null);
  updateCanvasNodeSelection();
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

  const newItem = {
    id: `item-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: resource.name,
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

  window.addEventListener("mousemove", (event) => {
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
      canvasItems: state.canvasItems.map((item) => ({ ...item }))
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

document.querySelector('[data-splitter="bottom"]')?.addEventListener("mousedown", () => {
  startDrag((event) => {
    const topBarHeight = 56;
    const height = window.innerHeight - topBarHeight - event.clientY;
    state.bottomHeight = clamp(height, constraints.bottomMin, constraints.bottomMax);
  });
});

document.querySelector('[data-splitter="bottom-right"]')?.addEventListener("mousedown", (mouseDownEvent) => {
  const bottomPanel = mouseDownEvent.currentTarget.parentElement.getBoundingClientRect();

  startDrag((event) => {
    const width = bottomPanel.right - event.clientX;
    state.bottomRightWidth = clamp(width, constraints.bottomRightMin, constraints.bottomRightMax);
  });
});

// ===== Tab Behavior =====
tabs.forEach((tab) => {
  function activateTab() {
    const name = tab.dataset.tab;

    tabs.forEach((item) => {
      const active = item === tab;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
      item.setAttribute("tabindex", active ? "0" : "-1");
    });

    Object.entries(panels).forEach(([panelName, panelEl]) => {
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
      const currentIndex = tabs.indexOf(tab);
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex = (currentIndex + direction + tabs.length) % tabs.length;
      tabs[nextIndex].focus();
    }
  });
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
  params.set("section", "project");
  params.set("source", "canvas");
  params.set("mode", "project-only");
  params.set("projectId", state.currentProject.id);
  window.location.href = `./settings.html?${params.toString()}`;
});

searchInput?.addEventListener("input", () => {
  state.searchTerm = searchInput.value;
  renderResources();
});

// ===== Initialization =====
function initialize() {
  // Get projectId from URL params
  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("projectId");

  if (!projectId) {
    console.error("No project ID provided");
    window.location.href = "./landing.html";
    return;
  }

  // Load projects from localStorage
  loadProjects();
  saveProjects();

  // Load this specific project
  if (!loadCurrentProject(projectId)) {
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
  saveCurrentProject();

  // Update UI with project info
  renderProjectName();
  updateTimestamp();

  // Initialize layout
  applySizes();
  initializeCanvasInteractions();
  renderCanvasItems();
  renderCanvasView();

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

try {
  initialize();
} catch (error) {
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
}
