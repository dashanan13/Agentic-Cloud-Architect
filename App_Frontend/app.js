// ===== UI Element References =====
const appEl = document.getElementById("app");
const screenProjects = document.getElementById("screen-projects");
const screenCanvas = document.getElementById("screen-canvas");
const btnCreateProject = document.getElementById("btn-create-project");
const btnBackProjects = document.getElementById("btn-back-projects");
const modalCreateProject = document.getElementById("modal-create-project");
const modalClose = document.getElementById("modal-close");
const modalCancel = document.getElementById("modal-cancel");
const modalCreate = document.getElementById("modal-create");
const createCloudSelect = document.getElementById("create-cloud");
const createNameInput = document.getElementById("create-name");
const projectNameDisplay = document.getElementById("project-name-display");
const projectTimestamp = document.getElementById("project-timestamp");
const resourceListEl = document.getElementById("resource-list");
const searchInput = document.getElementById("search-input");
const selectedResourceNameEl = document.getElementById("selected-resource-name");
const propertyContentEl = document.getElementById("property-content");
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
  leftWidth: 280,
  rightWidth: 320,
  bottomHeight: 220,
  bottomRightWidth: 320,
  selectedResource: null,
  searchTerm: ""
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

// ===== Utility Functions =====
function generateProjectName(cloud) {
  const now = new Date();
  const date = now.toISOString().slice(0, 10).replace(/-/g, "-");
  const time = now.toTimeString().slice(0, 8).replace(/:/g, "-");
  return `${cloud.toLowerCase()}-${date}-${time}`;
}

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

// ===== LocalStorage =====
function saveProjects() {
  localStorage.setItem("a3_projects", JSON.stringify(state.projects));
}

function loadProjects() {
  const stored = localStorage.getItem("a3_projects");
  state.projects = stored ? JSON.parse(stored) : [];
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

// ===== Screen Switching =====
function showProjectsScreen() {
  screenProjects.classList.add("is-active");
  screenCanvas.classList.remove("is-active");
  state.currentProject = null;
  renderProjectsList();
}

function showCanvasScreen(projectId) {
  if (!loadCurrentProject(projectId)) {
    return;
  }

  screenProjects.classList.remove("is-active");
  screenCanvas.classList.add("is-active");
  
  projectNameDisplay.textContent = state.currentProject.name;
  updateTimestamp();
  
  loadCatalogForCloud(state.currentProject.cloud).then(() => {
    renderResources();
  });
  
  updatePropertyPanel(null);
}

// ===== Project List =====
function renderProjectsList() {
  document.getElementById("projects-azure").innerHTML = "";
  document.getElementById("projects-aws").innerHTML = "";
  document.getElementById("projects-gcp").innerHTML = "";

  state.projects.forEach((project) => {
    const card = document.createElement("div");
    card.className = "project-card";
    
    const name = document.createElement("div");
    name.className = "project-card-name";
    name.textContent = project.name;
    
    const timestamp = document.createElement("div");
    timestamp.className = "project-card-timestamp";
    timestamp.textContent = formatTimestamp(project.lastSaved);
    
    const actions = document.createElement("div");
    actions.className = "project-card-actions";
    
    const selectBtn = document.createElement("button");
    selectBtn.className = "btn btn--sm btn--primary";
    selectBtn.textContent = "Open";
    selectBtn.addEventListener("click", () => showCanvasScreen(project.id));
    
    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn btn--sm btn--danger";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", () => deleteProject(project.id));
    
    actions.appendChild(selectBtn);
    actions.appendChild(deleteBtn);
    
    card.appendChild(name);
    card.appendChild(timestamp);
    card.appendChild(actions);
    
    const cloudPick = {
      Azure: "projects-azure",
      AWS: "projects-aws",
      GCP: "projects-gcp"
    }[project.cloud];
    
    if (cloudPick) {
      document.getElementById(cloudPick).appendChild(card);
    }
  });

  // Show empty messages if no projects
  ["projects-azure", "projects-aws", "projects-gcp"].forEach((id) => {
    const container = document.getElementById(id);
    if (container.children.length === 0) {
      const empty = document.createElement("div");
      empty.className = "project-empty";
      empty.textContent = "No projects yet";
      container.appendChild(empty);
    }
  });
}

// ===== Project Management =====
function createProject() {
  const cloud = createCloudSelect.value;
  const name = createNameInput.value.trim() || generateProjectName(cloud);

  if (!cloud) {
    alert("Please select a cloud provider");
    return;
  }

  const project = {
    id: `${cloud.toLowerCase()}-${Date.now()}`,
    name,
    cloud,
    lastSaved: Date.now()
  };

  state.projects.push(project);
  saveProjects();
  closeCreateModal();
  showCanvasScreen(project.id);
}

function deleteProject(projectId) {
  if (!confirm("Delete this project? This cannot be undone.")) {
    return;
  }

  state.projects = state.projects.filter((p) => p.id !== projectId);
  saveProjects();
  renderProjectsList();
}

function updateTimestamp() {
  if (state.currentProject) {
    state.currentProject.lastSaved = Date.now();
    saveCurrentProject();
    projectTimestamp.textContent = `Last saved: ${formatTimestamp(state.currentProject.lastSaved)}`;
  }
}

// ===== Modal =====
function openCreateModal() {
  modalCreateProject.classList.remove("is-hidden");
  createCloudSelect.value = "";
  createNameInput.value = generateProjectName("Azure");
  createCloudSelect.focus();
}

function closeCreateModal() {
  modalCreateProject.classList.add("is-hidden");
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

  const icon = document.createElement("img");
  icon.className = "resource-icon";
  icon.alt = `${resource.name} icon`;
  icon.src = encodeURI(`${iconRoot}/${category}/${resource.icon}`);
  icon.loading = "lazy";

  const name = document.createElement("span");
  name.className = "resource-name";
  name.textContent = resource.name;

  row.appendChild(icon);
  row.appendChild(name);
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
projectNameDisplay.addEventListener("blur", () => {
  const newName = projectNameDisplay.textContent.trim();
  if (newName && state.currentProject) {
    state.currentProject.name = newName;
    saveCurrentProject();
    updateTimestamp();
  } else {
    projectNameDisplay.textContent = state.currentProject?.name || "Unnamed";
  }
});

projectNameDisplay.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    projectNameDisplay.blur();
  }
});

// ===== Event Listeners =====
btnCreateProject.addEventListener("click", openCreateModal);
btnBackProjects.addEventListener("click", showProjectsScreen);
modalClose.addEventListener("click", closeCreateModal);
modalCancel.addEventListener("click", closeCreateModal);
modalCreate.addEventListener("click", createProject);

createCloudSelect.addEventListener("change", () => {
  if (!createNameInput.value || createNameInput.value.split("-")[0] === "azure" || createNameInput.value.split("-")[0] === "aws" || createNameInput.value.split("-")[0] === "gcp") {
    createNameInput.value = generateProjectName(createCloudSelect.value);
  }
});

searchInput?.addEventListener("input", () => {
  state.searchTerm = searchInput.value;
  renderResources();
});

// ===== Initialize =====
applySizes();
loadProjects();
showProjectsScreen();
