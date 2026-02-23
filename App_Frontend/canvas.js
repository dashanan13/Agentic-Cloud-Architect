// ===== UI Element References =====
const btnBackProjects = document.getElementById("btn-back-projects");
const projectNamePrefixDisplay = document.getElementById("project-name-prefix-display");
const projectNameDisplay = document.getElementById("project-name-suffix-display");
const projectTimestamp = document.getElementById("project-timestamp");
const resourceListEl = document.getElementById("resource-list");
const searchInput = document.getElementById("search-input");
const selectedResourceNameEl = document.getElementById("selected-resource-name");
const propertyContentEl = document.getElementById("property-content");
const appEl = document.getElementById("app");
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

const MAX_PROJECT_NAME_LENGTH = 27;

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
  return {
    ...project,
    cloud,
    name: `${prefix}${suffix}`,
    lastSaved: Number(project.lastSaved) || Date.now()
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

function updateTimestamp() {
  if (state.currentProject) {
    state.currentProject.lastSaved = Date.now();
    saveCurrentProject();
    projectTimestamp.textContent = `Last saved: ${formatTimestamp(state.currentProject.lastSaved)}`;
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
btnBackProjects.addEventListener("click", () => {
  window.location.href = "./landing.html";
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
  saveCurrentProject();

  // Update UI with project info
  renderProjectName();
  updateTimestamp();

  // Initialize layout
  applySizes();

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
