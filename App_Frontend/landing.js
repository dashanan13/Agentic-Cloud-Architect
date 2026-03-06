// ===== UI Element References =====
const introCloudSelect = document.getElementById("intro-cloud");
const introNameInput = document.getElementById("intro-name");
const introProjectIdInput = document.getElementById("intro-project-id");
const introAppDescriptionInput = document.getElementById("intro-app-description");
const introAppTypeSelect = document.getElementById("intro-app-type");
const introIacLanguageInputs = Array.from(document.querySelectorAll('input[name="intro-iac-language"]'));
const introPrefix = document.getElementById("intro-prefix");
const introNameHint = document.getElementById("intro-name-hint");
const createProjectMessage = document.getElementById("create-project-message");
const descriptionQualityMeter = document.getElementById("description-quality-meter");
const descriptionQualityStatus = document.getElementById("description-quality-status");
const descriptionQualityFill = document.getElementById("description-quality-fill");
const btnImproveDescription = document.getElementById("btn-improve-description");
const createProjectSectionEl = document.querySelector(".intro-column--create");
const createProjectToggleBtn = document.getElementById("create-project-toggle");
const createProjectContentEl = document.getElementById("create-project-content");
const btnIntroCreate = document.getElementById("btn-intro-create");
const btnAppSettings = document.getElementById("btn-app-settings");
const cloudHeaders = Array.from(document.querySelectorAll(".cloud-header"));

// ===== State =====
const state = {
  projects: [],
  descriptionQuality: {
    index: 0,
    level: "Poor",
    status: "idle",
    score: 0
  },
  descriptionEvalToken: 0,
  descriptionEvalTimer: null,
  descriptionImproveToken: 0
};

const MAX_PROJECT_NAME_LENGTH = 50;
const DESCRIPTION_LEVELS = ["Poor", "Minimal", "Adequate", "Informative", "Rich", "Perfect"];
const MIN_DESCRIPTION_LEVEL_INDEX = 2;
const DESCRIPTION_EVAL_DELAY_MS = 4000;
const DEFAULT_IAC_LANGUAGE = "bicep";

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

function generateTimestamp() {
  const now = new Date();
  const pad = (n, len = 2) => String(n).padStart(len, "0");
  return (
    now.getFullYear().toString() +
    pad(now.getMonth() + 1) +
    pad(now.getDate()) +
    pad(now.getHours()) +
    pad(now.getMinutes()) +
    pad(now.getSeconds()) +
    pad(now.getMilliseconds(), 3)
  );
}

function generateProjectId(projectName) {
  return `${projectName}-${generateTimestamp()}`;
}

function updateProjectIdPreview() {
  if (!introProjectIdInput) {
    return;
  }
  const cloud = introCloudSelect.value;
  const suffix = introNameInput.value.trim();
  if (!cloud || !suffix) {
    introProjectIdInput.value = "";
    return;
  }
  const fullName = normalizeProjectName(cloud, introNameInput.value);
  introProjectIdInput.value = generateProjectId(fullName);
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

function getSelectedIacLanguage(inputs, fallback = DEFAULT_IAC_LANGUAGE) {
  const selected = (inputs || []).find((input) => input.checked)?.value;
  return String(selected || fallback).trim().toLowerCase() || fallback;
}

function setSelectedIacLanguage(inputs, value = DEFAULT_IAC_LANGUAGE) {
  if (!inputs || !inputs.length) {
    return;
  }

  const target = String(value || DEFAULT_IAC_LANGUAGE).trim().toLowerCase() || DEFAULT_IAC_LANGUAGE;
  let matched = false;

  inputs.forEach((input) => {
    const normalized = String(input.value || "").trim().toLowerCase();
    input.checked = normalized === target;
    if (input.checked) {
      matched = true;
    }
  });

  if (!matched) {
    inputs[0].checked = true;
  }
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

function setQualityStatus(message, type = "") {
  if (!descriptionQualityStatus) {
    return;
  }

  descriptionQualityStatus.textContent = message;
  descriptionQualityStatus.classList.remove("is-error", "is-info");
  if (type === "error") {
    descriptionQualityStatus.classList.add("is-error");
  } else if (type === "info") {
    descriptionQualityStatus.classList.add("is-info");
  }
}

function updateQualityMeter(levelIndex) {
  if (!descriptionQualityMeter) {
    return;
  }

  const markers = Array.from(descriptionQualityMeter.querySelectorAll(".quality-bar__marker"));
  const labels = Array.from(descriptionQualityMeter.querySelectorAll(".quality-label"));
  const allItems = [...markers, ...labels];

  allItems.forEach((item) => {
    const itemLevel = Number(item.dataset.level);
    const level = Number.isFinite(itemLevel) ? itemLevel : 0;
    item.classList.toggle("is-active", level === levelIndex);
  });

  if (descriptionQualityFill) {
    const maxIndex = Math.max(DESCRIPTION_LEVELS.length - 1, 1);
    const percent = Math.round((levelIndex / maxIndex) * 100);
    descriptionQualityFill.style.width = `${percent}%`;
  }
}

function updateCreateButtonState() {
  if (!btnIntroCreate) {
    return;
  }

  const hasCloud = Boolean(introCloudSelect?.value);
  const hasAppType = Boolean(introAppTypeSelect?.value);
  const hasPrereqs = hasCloud && hasAppType;
  const isAdequate = state.descriptionQuality.index >= MIN_DESCRIPTION_LEVEL_INDEX;
  btnIntroCreate.hidden = !hasPrereqs;
  btnIntroCreate.disabled = !hasPrereqs || !isAdequate;
  btnIntroCreate.title = isAdequate
    ? "Create Project"
    : "Description must be Adequate or better";
}

function setDescriptionQuality(levelIndex, levelLabel, status = "idle", statusMessage = "", score = 0) {
  const safeIndex = Math.min(Math.max(Number(levelIndex) || 0, 0), DESCRIPTION_LEVELS.length - 1);
  const label = levelLabel || DESCRIPTION_LEVELS[safeIndex];
  const safeScore = Number.isFinite(Number(score)) ? Number(score) : 0;
  state.descriptionQuality = {
    index: safeIndex,
    level: label,
    status,
    score: safeScore
  };

  updateQualityMeter(safeIndex);
  if (statusMessage) {
    setQualityStatus(statusMessage, status === "checking" ? "info" : status === "error" ? "error" : "");
  } else {
    setQualityStatus(`Quality: ${label}`);
  }
  updateCreateButtonState();
}

function scheduleDescriptionEvaluation() {
  if (!introAppDescriptionInput) {
    return;
  }

  const description = String(introAppDescriptionInput.value || "").trim();
  if (state.descriptionEvalTimer) {
    window.clearTimeout(state.descriptionEvalTimer);
  }

  if (!description) {
    setDescriptionQuality(0, "Poor", "idle", "Quality: Not evaluated");
    return;
  }

  setQualityStatus("Quality: Evaluating...", "info");
  state.descriptionEvalTimer = window.setTimeout(() => {
    evaluateDescription(description).catch(() => {
      setDescriptionQuality(0, "Poor", "error", "Quality check failed");
    });
  }, DESCRIPTION_EVAL_DELAY_MS);
}

async function evaluateDescription(description) {
  const token = ++state.descriptionEvalToken;
  const payload = {
    description: String(description || "").trim(),
    appType: String(introAppTypeSelect?.value || "").trim(),
    cloud: String(introCloudSelect?.value || "").trim()
  };

  const response = await fetch("/api/description/evaluate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(payload)
  });

  if (token !== state.descriptionEvalToken) {
    return;
  }

  if (!response.ok) {
    throw new Error("Quality check failed");
  }

  const result = await response.json();
  if (!result?.ok || result?.skipped) {
    setDescriptionQuality(0, "Poor", "error", "Quality check unavailable");
    return;
  }

  setDescriptionQuality(result.levelIndex, result.level, "idle", `Quality: ${result.level}`, result.score);
}

async function improveDescription() {
  if (!introAppDescriptionInput || !btnImproveDescription) {
    return;
  }

  const rawDescription = String(introAppDescriptionInput.value || "").trim();
  if (!rawDescription) {
    setCreateMessage("Add a description first.", "error");
    return;
  }

  const token = ++state.descriptionImproveToken;
  btnImproveDescription.disabled = true;
  setQualityStatus("Improving description...", "info");

  try {
    const response = await fetch("/api/description/improve", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        description: rawDescription,
        appType: String(introAppTypeSelect?.value || "").trim(),
        cloud: String(introCloudSelect?.value || "").trim()
      })
    });

    if (!response.ok) {
      throw new Error("Unable to improve description.");
    }

    const payload = await response.json();
    if (token !== state.descriptionImproveToken) {
      return;
    }

    const improved = String(payload?.improved || "").trim();
    if (!improved) {
      throw new Error("AI did not return an improved description.");
    }

    introAppDescriptionInput.value = improved;
    scheduleDescriptionEvaluation();
  } catch (error) {
    setQualityStatus("Quality: Improve failed", "error");
    setCreateMessage(error.message || "Improve failed.", "error");
  } finally {
    btnImproveDescription.disabled = false;
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

async function bootstrapFoundryDefaultsOnLoad() {
  try {
    await fetch("/api/foundry/bootstrap-default", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      }
    });
  } catch {
  }
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
  const iacLanguage = getSelectedIacLanguage(introIacLanguageInputs);

  if (!cloud) {
    setCreateMessage("Please select a cloud provider.", "error");
    return;
  }

  if (state.descriptionQuality.index < MIN_DESCRIPTION_LEVEL_INDEX) {
    setCreateMessage("Description must be Adequate or better.", "error");
    return;
  }

  const suffix = introNameInput.value.trim();
  const maxSuffixLength = getMaxSuffixLength(cloud);
  if (suffix.length > maxSuffixLength) {
    setCreateMessage(`Project name is too long. Max ${MAX_PROJECT_NAME_LENGTH} chars including prefix.`, "error");
    return;
  }

  const name = normalizeProjectName(cloud, introNameInput.value);

  const projectId = introProjectIdInput?.value || generateProjectId(name);

  const project = {
    id: projectId,
    name,
    cloud,
    applicationDescription,
    applicationType,
    iacLanguage,
    applicationDescriptionQuality: state.descriptionQuality.level,
    applicationDescriptionQualityIndex: state.descriptionQuality.index,
    applicationDescriptionQualityScore: state.descriptionQuality.score,
    lastSaved: Date.now()
  };

  try {
    const response = await fetch("/api/project/save", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        create: true,
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
    if (introProjectIdInput) {
      introProjectIdInput.value = "";
    }
    if (introAppDescriptionInput) {
      introAppDescriptionInput.value = "";
    }
    if (introAppTypeSelect) {
      introAppTypeSelect.value = "";
    }
    setSelectedIacLanguage(introIacLanguageInputs, DEFAULT_IAC_LANGUAGE);
    introCloudSelect.value = "";
    updateNameControlsForCloud("");
    setDescriptionQuality(0, "Poor", "idle", "Quality: Not evaluated");
    updateCreateButtonState();
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

function setCreateProjectExpanded(expanded) {
  if (!createProjectSectionEl || !createProjectToggleBtn || !createProjectContentEl) {
    return;
  }

  createProjectSectionEl.classList.toggle("is-expanded", expanded);
  createProjectToggleBtn.setAttribute("aria-expanded", String(expanded));
  createProjectContentEl.toggleAttribute("hidden", !expanded);

  const indicator = createProjectToggleBtn.querySelector(".collapse-indicator");
  if (indicator) {
    indicator.textContent = expanded ? "Collapse" : "Expand";
  }
}

// ===== Event Listeners =====
btnIntroCreate.addEventListener("click", async () => {
  await createProject();
});

createProjectToggleBtn?.addEventListener("click", () => {
  const expanded = createProjectToggleBtn.getAttribute("aria-expanded") === "true";
  setCreateProjectExpanded(!expanded);
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
    updateProjectIdPreview();
    return;
  }

  const rawValue = introNameInput.value.trim();
  if (!rawValue) {
    introNameInput.value = generateDefaultSuffix().slice(0, getMaxSuffixLength(cloud));
  } else {
    const normalized = normalizeProjectName(cloud, rawValue);
    introNameInput.value = normalized.slice(getProjectPrefix(cloud).length);
  }
  updateProjectIdPreview();
  scheduleDescriptionEvaluation();
  updateCreateButtonState();
});

introAppTypeSelect.addEventListener("change", () => {
  scheduleDescriptionEvaluation();
  updateCreateButtonState();
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
  updateProjectIdPreview();
});

introAppDescriptionInput.addEventListener("input", () => {
  setCreateMessage("");
  scheduleDescriptionEvaluation();
});

btnImproveDescription?.addEventListener("click", async () => {
  await improveDescription();
});

// ===== Initialization =====
async function initialize() {
  await bootstrapFoundryDefaultsOnLoad();

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
  setSelectedIacLanguage(introIacLanguageInputs, DEFAULT_IAC_LANGUAGE);
  setCreateProjectExpanded(false);
  setDescriptionQuality(0, "Poor", "idle", "Quality: Not evaluated");
  updateCreateButtonState();
}

initialize();
