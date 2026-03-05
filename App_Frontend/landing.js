// ===== UI Element References =====
const introCloudSelect = document.getElementById("intro-cloud");
const introNameInput = document.getElementById("intro-name");
const introAppDescriptionInput = document.getElementById("intro-app-description");
const introAppTypeSelect = document.getElementById("intro-app-type");
const introPrefix = document.getElementById("intro-prefix");
const introNameHint = document.getElementById("intro-name-hint");
const createProjectMessage = document.getElementById("create-project-message");
const btnIntroCreate = document.getElementById("btn-intro-create");
const btnAppSettings = document.getElementById("btn-app-settings");
const cloudHeaders = Array.from(document.querySelectorAll(".cloud-header"));

// ===== State =====
const state = {
  projects: []
};

const MAX_PROJECT_NAME_LENGTH = 50;

// ===== Utility Functions =====
function generateDefaultSuffix() {
  const now = new Date();
  const date = now.toISOString().slice(0, 10).replace(/-/g, "");
  const time = now.toTimeString().slice(0, 5).replace(/:/g, "");
  return `proj-${date}-${time}`;
}

function formatTimestamp(ms) {
  const date = new Date(ms);
  return date.toLocaleString();
}

function getProjectPrefix(cloud) {
  return `${cloud}-`;
}

function getMaxSuffixLength(cloud) {
  if (!cloud) {
    return MAX_PROJECT_NAME_LENGTH;
  }
  return Math.max(1, MAX_PROJECT_NAME_LENGTH - getProjectPrefix(cloud).length);
}

function normalizeProjectName(cloud, rawName) {
  const prefix = getProjectPrefix(cloud);
  const normalized = (rawName || "").trim();
  const lowerPrefix = prefix.toLowerCase();

  let suffix = normalized;
  if (suffix.toLowerCase().startsWith(lowerPrefix)) {
    suffix = suffix.slice(prefix.length);
  }
  suffix = suffix.replace(/^[-\s]+/, "").trim();

  const maxSuffixLength = getMaxSuffixLength(cloud);
  if (!suffix) {
    suffix = generateDefaultSuffix();
  }
  suffix = suffix.slice(0, maxSuffixLength);

  return `${prefix}${suffix}`;
}

function sanitizeProject(project) {
  if (!project || typeof project !== "object") {
    return null;
  }

  const cloud = ["Azure", "AWS", "GCP"].includes(project.cloud) ? project.cloud : null;
  if (!cloud || !project.id) {
    return null;
  }

  return {
    ...project,
    name: normalizeProjectName(cloud, project.name),
    lastSaved: Number(project.lastSaved) || Date.now()
  };
}

function setCreateMessage(message, type = "") {
  if (!createProjectMessage) {
    return;
  }

  createProjectMessage.textContent = message;
  createProjectMessage.classList.remove("is-error", "is-success");
  if (type) {
    createProjectMessage.classList.add(type === "error" ? "is-error" : "is-success");
  }
}

function updateNameControlsForCloud(cloud) {
  if (!cloud) {
    introPrefix.textContent = "<Cloud>-";
    introNameInput.maxLength = MAX_PROJECT_NAME_LENGTH;
    introNameHint.textContent = "Select cloud to lock prefix.";
    return;
  }

  const prefix = getProjectPrefix(cloud);
  const maxSuffixLength = getMaxSuffixLength(cloud);

  introPrefix.textContent = prefix;
  introNameInput.maxLength = maxSuffixLength;
  introNameHint.textContent = `Prefix is fixed to ${prefix}. You can type up to ${maxSuffixLength} chars.`;
}

// ===== Projects API =====
async function loadProjects() {
  const response = await fetch("/api/projects", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load projects from files.");
  }

  const payload = await response.json();
  const projects = Array.isArray(payload?.projects) ? payload.projects : [];
  state.projects = projects.map(sanitizeProject).filter(Boolean);
}

// ===== Project List Rendering =====
function renderProjectsList() {
  document.getElementById("projects-azure").innerHTML = "";
  document.getElementById("projects-aws").innerHTML = "";
  document.getElementById("projects-gcp").innerHTML = "";

  const cloudSections = {
    Azure: document.getElementById("projects-azure"),
    AWS: document.getElementById("projects-aws"),
    GCP: document.getElementById("projects-gcp")
  };

  state.projects.forEach((project) => {
    const item = document.createElement("div");
    item.className = "project-item";

    const nameDiv = document.createElement("div");
    nameDiv.className = "project-item-name";
    nameDiv.textContent = project.name;

    const meta = document.createElement("div");
    meta.className = "project-item-meta";

    const timeDiv = document.createElement("span");
    timeDiv.className = "project-item-time";
    timeDiv.textContent = formatTimestamp(project.lastSaved);

    const actions = document.createElement("div");
    actions.className = "project-item-actions";

    const openBtn = document.createElement("button");
    openBtn.textContent = "Open";
    openBtn.title = "Open";
    openBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openProject(project.id);
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "delete";
    deleteBtn.textContent = "Delete";
    deleteBtn.title = "Delete";
    deleteBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      deleteProject(project.id);
    });

    actions.appendChild(openBtn);
    actions.appendChild(deleteBtn);
    meta.appendChild(timeDiv);
    meta.appendChild(actions);
    item.appendChild(nameDiv);
    item.appendChild(meta);

    // Click anywhere on item to open
    item.addEventListener("click", () => openProject(project.id));

    cloudSections[project.cloud]?.appendChild(item);
  });

  // Show empty messages if no projects
  Object.entries(cloudSections).forEach(([cloud, container]) => {
    if (container.children.length === 0) {
      const empty = document.createElement("div");
      empty.className = "projects-empty";
      empty.textContent = `No ${cloud} projects yet`;
      container.appendChild(empty);
    } else {
      container.classList.add("is-expanded");
    }
  });
}

// ===== Project Management =====
function openProject(projectId) {
  window.location.href = `./canvas.html?projectId=${encodeURIComponent(projectId)}`;
}

function openSettings({ section = "app", mode = "app-only" } = {}) {
  const params = new URLSearchParams();
  params.set("section", section);
  params.set("source", "landing");
  params.set("mode", mode);
  window.location.href = `./settings.html?${params.toString()}`;
}

async function createProject() {
  const cloud = introCloudSelect.value;
  const applicationDescription = String(introAppDescriptionInput?.value || "").trim();
  const applicationType = String(introAppTypeSelect?.value || "").trim();

  if (!cloud) {
    setCreateMessage("Please select a cloud provider.", "error");
    return;
  }

  const suffix = introNameInput.value.trim();
  const maxSuffixLength = getMaxSuffixLength(cloud);
  if (suffix.length > maxSuffixLength) {
    setCreateMessage(`Project name is too long. Max ${MAX_PROJECT_NAME_LENGTH} chars including prefix.`, "error");
    return;
  }

  const name = normalizeProjectName(cloud, introNameInput.value);

  const project = {
    id: `${cloud.toLowerCase()}-${Date.now()}`,
    name,
    cloud,
    applicationDescription,
    applicationType,
    lastSaved: Date.now()
  };

  try {
    const response = await fetch("/api/project/save", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        project,
        canvasState: {
          canvasView: { x: 0, y: 0, zoom: 1 },
          canvasItems: [],
          canvasConnections: []
        }
      })
    });

    if (!response.ok) {
      throw new Error("Unable to create project files.");
    }

    await loadProjects();
    setCreateMessage("");
    introNameInput.value = "";
    if (introAppDescriptionInput) {
      introAppDescriptionInput.value = "";
    }
    if (introAppTypeSelect) {
      introAppTypeSelect.value = "";
    }
    introCloudSelect.value = "";
    updateNameControlsForCloud("");
    renderProjectsList();
    openProject(project.id);
  } catch (error) {
    setCreateMessage(error.message || "Failed to create project.", "error");
  }
}

async function deleteProject(projectId) {
  if (!confirm("Delete this project? This cannot be undone.")) {
    return;
  }

  try {
    const response = await fetch(`/api/project/${encodeURIComponent(projectId)}`, {
      method: "DELETE"
    });
    if (!response.ok) {
      throw new Error("Unable to delete project files.");
    }

    await loadProjects();
    renderProjectsList();
  } catch (error) {
    setCreateMessage(error.message || "Failed to delete project.", "error");
  }
}

// ===== Accordion Behavior =====
function setCloudHeaderExpanded(header, expanded) {
  if (!header) {
    return;
  }

  header.classList.toggle("is-expanded", expanded);
  const indicator = header.querySelector(".collapse-indicator");
  if (indicator) {
    indicator.textContent = expanded ? "Collapse" : "Expand";
  }
}

function toggleCloudSection(cloud) {
  const header = document.querySelector(`.cloud-header[data-cloud="${cloud}"]`);
  const isExpanded = header?.classList.contains("is-expanded");
  
  if (isExpanded) {
    setCloudHeaderExpanded(header, false);
  } else {
    cloudHeaders.forEach((otherHeader) => {
      if (otherHeader !== header) {
        setCloudHeaderExpanded(otherHeader, false);
      }
    });
    setCloudHeaderExpanded(header, true);
  }
}

// ===== Event Listeners =====
btnIntroCreate.addEventListener("click", async () => {
  await createProject();
});

cloudHeaders.forEach((header) => {
  header.addEventListener("click", (e) => {
    e.preventDefault();
    const cloud = header.dataset.cloud;
    toggleCloudSection(cloud);
  });
});

btnAppSettings?.addEventListener("click", () => {
  openSettings({ section: "app", mode: "app-only" });
});
// Auto-generate name when cloud changes
introCloudSelect.addEventListener("change", () => {
  const cloud = introCloudSelect.value;
  updateNameControlsForCloud(cloud);
  setCreateMessage("");

  if (!cloud) {
    return;
  }

  const rawValue = introNameInput.value.trim();
  if (!rawValue) {
    introNameInput.value = generateDefaultSuffix().slice(0, getMaxSuffixLength(cloud));
    return;
  }

  const normalized = normalizeProjectName(cloud, rawValue);
  introNameInput.value = normalized.slice(getProjectPrefix(cloud).length);
});

introNameInput.addEventListener("input", () => {
  const cloud = introCloudSelect.value;
  if (!cloud) {
    setCreateMessage("");
    return;
  }

  const maxSuffixLength = getMaxSuffixLength(cloud);
  if (introNameInput.value.length > maxSuffixLength) {
    setCreateMessage(`Project name is too long. Max ${MAX_PROJECT_NAME_LENGTH} chars including prefix.`, "error");
  } else {
    setCreateMessage("");
  }

  const normalized = normalizeProjectName(cloud, introNameInput.value);
  introNameInput.value = normalized.slice(getProjectPrefix(cloud).length);
});

// ===== Initialization =====
async function initialize() {
  try {
    await loadProjects();
    renderProjectsList();
    setCreateMessage("");
  } catch (error) {
    state.projects = [];
    renderProjectsList();
    setCreateMessage(error.message || "Unable to load projects.", "error");
  }

  updateNameControlsForCloud(introCloudSelect.value);
}

initialize();
