// ===== UI Element References =====
const cloudSelect = document.getElementById("landing-cloud");
const nameInput = document.getElementById("landing-name");
const projectIdInput = document.getElementById("landing-project-id");
const descriptionInput = document.getElementById("landing-description");
const descriptionPlaceholder = document.getElementById("description-placeholder");
const appTypeSelect = document.getElementById("landing-app-type");
const iacLanguageInputs = Array.from(document.querySelectorAll('input[name="landing-iac-language"]'));
const nameHint = document.getElementById("landing-name-hint");
const messageEl = document.getElementById("landing-message");
const qualityBar = document.getElementById("landing-quality-bar");
const qualityStatus = document.getElementById("landing-quality-status");
const btnCreate = document.getElementById("btn-landing-create");
const btnAppSettings = document.getElementById("btn-app-settings");
const projectsAzureContainer = document.getElementById("projects-azure");

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
const DEFAULT_IAC_LANGUAGE = "bicep";
const DESCRIPTION_LEVELS = ["Poor", "Minimal", "Adequate", "Informative", "Rich", "Perfect"];
const MIN_DESCRIPTION_LEVEL_INDEX = 2; // Adequate
const DESCRIPTION_EVAL_DELAY_MS = 4000;

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

function normalizeProjectName(cloud, rawName) {
  const prefix = getProjectPrefix(cloud);
  const normalized = (rawName || "").trim();
  const lowerPrefix = prefix.toLowerCase();

  let suffix = normalized;
  if (suffix.toLowerCase().startsWith(lowerPrefix)) {
    suffix = suffix.slice(prefix.length);
  }
  suffix = suffix.replace(/^[-\s]+/, "").trim();

  const maxSuffixLength = Math.max(1, MAX_PROJECT_NAME_LENGTH - prefix.length);
  if (!suffix) {
    suffix = generateDefaultSuffix();
  }
  suffix = suffix.slice(0, maxSuffixLength);

  return `${prefix}${suffix}`;
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

function getSelectedIacLanguage() {
  const selected = Array.from(document.querySelectorAll('input[name="landing-iac-language"]')).find((input) => input.checked)?.value;
  return String(selected || DEFAULT_IAC_LANGUAGE).trim().toLowerCase() || DEFAULT_IAC_LANGUAGE;
}

function sanitizeProject(project) {
  if (!project || typeof project !== "object") {
    return null;
  }
  const cloud = ["Azure"].includes(project.cloud) ? project.cloud : null;
  const name = String(project.name || "").trim().slice(0, 200);
  const id = String(project.id || "").trim().slice(0, 500);
  const lastSaved = Number.isFinite(project.lastSaved) ? project.lastSaved : 0;

  return { id, name, cloud, lastSaved };
}

function updateProjectIdPreview() {
  if (!projectIdInput) {
    return;
  }
  const cloud = cloudSelect.value;
  const suffix = nameInput.value.trim();
  if (!cloud || !suffix) {
    projectIdInput.value = "";
    return;
  }
  const fullName = normalizeProjectName(cloud, nameInput.value);
  projectIdInput.value = generateProjectId(fullName);
}

// ===== Message Handling =====
function setMessage(text, type = "info") {
  if (!messageEl) return;
  messageEl.textContent = text;
  messageEl.className = `message ${type ? `message--${type}` : ""}`.trim();
}

function clearMessage() {
  if (messageEl) {
    messageEl.textContent = "";
    messageEl.className = "message";
  }
}

// ===== Description Quality =====
function setDescriptionQuality(index, level, status, label) {
  state.descriptionQuality = { index, level, status, score: (index + 1) * (100 / DESCRIPTION_LEVELS.length) };
  
  if (qualityStatus) {
    qualityStatus.textContent = label || `Quality: ${level}`;
  }

  if (qualityBar) {
    const markers = Array.from(qualityBar.querySelectorAll(".q"));
    markers.forEach((marker, i) => {
      marker.style.opacity = i <= index ? "1" : "0.3";
    });
  }
}

function updateDescriptionQuality() {
  const text = (descriptionInput.value || "").trim();
  const length = text.length;

  // Simple heuristic for demo/testing
  let levelIndex = 0;
  if (length > 500) levelIndex = 5;
  else if (length > 400) levelIndex = 4;
  else if (length > 300) levelIndex = 3;
  else if (length > 200) levelIndex = 2;
  else if (length > 50) levelIndex = 1;

  setDescriptionQuality(levelIndex, DESCRIPTION_LEVELS[levelIndex], "active");
}

function scheduleDescriptionEvaluation() {
  if (!descriptionInput) return;
  const description = String(descriptionInput.value || "").trim();
  if (state.descriptionEvalTimer) {
    window.clearTimeout(state.descriptionEvalTimer);
  }

  if (!description) {
    setDescriptionQuality(0, "Poor", "idle", "Quality: Not evaluated");
    updateCreateButtonState();
    return;
  }

  if (qualityStatus) setMessage("", "info");
  if (qualityStatus) qualityStatus.textContent = "Quality: Evaluating...";

  state.descriptionEvalTimer = window.setTimeout(() => {
    evaluateDescription(description).catch(() => {
      setDescriptionQuality(0, "Poor", "error", "Quality check failed");
      updateCreateButtonState();
    });
  }, DESCRIPTION_EVAL_DELAY_MS);
}

async function evaluateDescription(description) {
  const token = ++state.descriptionEvalToken;
  const payload = {
    description: String(description || "").trim(),
    appType: String(appTypeSelect?.value || "").trim(),
    cloud: String(cloudSelect?.value || "").trim()
  };

  const response = await fetch("/api/description/evaluate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (token !== state.descriptionEvalToken) return;

  if (!response.ok) {
    throw new Error("Quality check failed");
  }

  const result = await response.json();
  if (!result?.ok || result?.skipped) {
    setDescriptionQuality(0, "Poor", "error", "Quality check unavailable");
    updateCreateButtonState();
    return;
  }

  setDescriptionQuality(result.levelIndex, result.level, "idle", `Quality: ${result.level}`);
  updateCreateButtonState();
}

async function improveDescription() {
  const btnImprove = document.getElementById("btn-improve-description");
  if (!descriptionInput || !btnImprove) return;

  const raw = String(descriptionInput.value || "").trim();
  if (!raw) {
    setMessage("Add a description first.", "error");
    return;
  }

  const token = ++state.descriptionImproveToken;
  btnImprove.disabled = true;
  if (qualityStatus) qualityStatus.textContent = "Improving description...";

  try {
    const response = await fetch("/api/description/improve", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ description: raw, appType: String(appTypeSelect?.value || "").trim(), cloud: String(cloudSelect?.value || "").trim() })
    });

    if (!response.ok) throw new Error("Unable to improve description.");

    const payload = await response.json();
    if (token !== state.descriptionImproveToken) return;

    const improved = String(payload?.improved || "").trim();
    if (!improved) throw new Error("AI did not return an improved description.");

    descriptionInput.value = improved;
    if (descriptionPlaceholder) descriptionPlaceholder.style.display = "none";
    scheduleDescriptionEvaluation();
  } catch (err) {
    if (qualityStatus) qualityStatus.textContent = "Quality: Improve failed";
    setMessage(err.message || "Improve failed.", "error");
  } finally {
    btnImprove.disabled = false;
  }
}

function updateCreateButtonState() {
  if (!btnCreate) return;
  const hasCloud = Boolean(cloudSelect?.value);
  const hasAppType = Boolean(appTypeSelect?.value);
  const hasPrereqs = hasCloud && hasAppType;
  const isAdequate = state.descriptionQuality.index >= MIN_DESCRIPTION_LEVEL_INDEX;
  // Always show the Create button, but disable it until prerequisites and quality met
  btnCreate.hidden = false;
  btnCreate.disabled = !hasPrereqs || !isAdequate;
  btnCreate.title = isAdequate ? "Create Project" : "Description must be Adequate or better";
}

// ===== API Calls =====
async function loadProjects() {
  try {
    const response = await fetch("/api/projects", { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Unable to load projects from files.");
    }

    const payload = await response.json();
    const projects = Array.isArray(payload?.projects) ? payload.projects : [];
    state.projects = projects.map(sanitizeProject).filter(Boolean);
  } catch (error) {
    state.projects = [];
    throw error;
  }
}

async function bootstrapFoundryDefaults() {
  try {
    await fetch("/api/foundry/bootstrap-default", {
      method: "POST",
      headers: { "Content-Type": "application/json" }
    });
  } catch {
    // Silently ignore errors
  }
}

async function createProject(payload) {
  const response = await fetch("/api/project/save", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload)
  });

  if (!response.ok) {
    const error = await response.json().catch(() => ({}));
    throw new Error(error.detail || "Failed to create project.");
  }

  return await response.json();
}

async function deleteProject(projectId) {
  const response = await fetch(`/api/project/${projectId}`, { method: "DELETE" });
  if (!response.ok) {
    throw new Error("Failed to delete project.");
  }
  return await response.json();
}

// ===== Project Rendering =====
function renderProjectsList() {
  if (projectsAzureContainer) {
    projectsAzureContainer.innerHTML = "";
  }

  state.projects.forEach((project) => {
    if (project.cloud !== "Azure") return;

    const item = document.createElement("div");
    item.className = "project-item";

    const infoDiv = document.createElement("div");
    const nameSpan = document.createElement("strong");
    nameSpan.textContent = project.name;
    const dateSpan = document.createElement("span");
    dateSpan.className = "muted";
    dateSpan.textContent = formatTimestamp(project.lastSaved);
    // make timestamp appear on its own line with a bit of spacing
    dateSpan.style.display = "block";
    dateSpan.style.marginTop = "6px";

    infoDiv.appendChild(nameSpan);
    infoDiv.appendChild(document.createElement("br"));
    infoDiv.appendChild(dateSpan);

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "btn-plain";
    deleteBtn.textContent = "Delete";
    deleteBtn.addEventListener("click", async (e) => {
      e.stopPropagation();
      if (!confirm(`Delete project "${project.name}"?`)) return;

      try {
        await deleteProject(project.id);
        await loadProjects();
        renderProjectsList();
        setMessage("Project deleted successfully.", "success");
      } catch (error) {
        setMessage(error.message || "Failed to delete project.", "error");
      }
    });

    item.appendChild(infoDiv);
    item.appendChild(deleteBtn);

    item.addEventListener("click", () => openProject(project.id));

    if (projectsAzureContainer) {
      projectsAzureContainer.appendChild(item);
    }
  });
}

function openProject(projectId) {
  window.location.href = `/canvas.html?projectId=${encodeURIComponent(projectId)}`;
}

// ===== Form Handlers =====
function selectIacLanguage(value, event) {
  if (event) {
    event.preventDefault();
  }

  const buttons = document.querySelectorAll(".toggle button");
  buttons.forEach((btn) => {
    btn.classList.toggle("active", btn.dataset.value === value);
  });

  const input = iacLanguageInputs.find((inp) => inp.value === value);
  if (input) {
    input.checked = true;
  }
}

// ===== Initialization =====
async function initialize() {
  try {
    // Load projects and bootstrap Foundry in parallel
    await Promise.all([
      bootstrapFoundryDefaults(),
      loadProjects()
    ]);
    renderProjectsList();
    clearMessage();
  } catch (error) {
    state.projects = [];
    renderProjectsList();
    setMessage(error.message || "Unable to load projects.", "error");
  }

  setDescriptionQuality(0, "Poor", "idle", "Quality: Not evaluated");
  if (cloudSelect) {
    cloudSelect.value = cloudSelect.value || "Azure";
  }
  updateCreateButtonState();
}

// ===== Event Listeners =====
if (cloudSelect) {
  cloudSelect.addEventListener("change", (e) => {
    updateProjectIdPreview();
    updateCreateButtonState();
  });
}

if (nameInput) {
  nameInput.addEventListener("input", updateProjectIdPreview);
}

if (appTypeSelect) {
  appTypeSelect.addEventListener("change", updateCreateButtonState);
}

if (descriptionInput) {
  descriptionInput.addEventListener("input", () => {
    if (descriptionPlaceholder) {
      descriptionPlaceholder.style.display = descriptionInput.value ? "none" : "block";
    }
    updateDescriptionQuality();
    scheduleDescriptionEvaluation();
    updateCreateButtonState();
  });
}

if (btnCreate) {
  btnCreate.addEventListener("click", async () => {
    const cloud = cloudSelect.value.trim();
    const name = nameInput.value.trim();
    const description = descriptionInput.value.trim();

    if (!cloud) {
      setMessage("Please select a cloud provider.", "error");
      return;
    }

    if (!name) {
      setMessage("Please enter a project name.", "error");
      return;
    }

    const fullName = normalizeProjectName(cloud, name);
    const projectId = generateProjectId(fullName);

    try {
      btnCreate.disabled = true;
      setMessage("Creating project...", "info");

      const payload = {
        project: {
          id: projectId,
          name: fullName,
          cloud: cloud,
          applicationType: appTypeSelect.value.trim(),
          applicationDescription: description,
          iacLanguage: getSelectedIacLanguage(),
          lastSaved: Date.now()
        },
        canvasState: {},
        create: true
      };

      await createProject(payload);

      nameInput.value = "";
      projectIdInput.value = "";
      descriptionInput.value = "";
      if (descriptionPlaceholder) {
        descriptionPlaceholder.style.display = "block";
      }
      appTypeSelect.value = "";
      cloudSelect.value = "Azure";
      setDescriptionQuality(0, "Poor", "idle", "Quality: Not evaluated");

      await loadProjects();
      renderProjectsList();
      setMessage("Project created successfully!", "success");
    } catch (error) {
      setMessage(error.message || "Failed to create project.", "error");
    } finally {
      btnCreate.disabled = false;
    }
  });
}

if (btnAppSettings) {
  btnAppSettings.addEventListener("click", () => {
    window.location.href = "/settings.html";
  });
}

const btnImproveDesc = document.getElementById("btn-improve-description");
if (btnImproveDesc) {
  btnImproveDesc.addEventListener("click", improveDescription);
}

// ===== Helper Functions =====
function toggleSection(header) {
  const body = header.nextElementSibling;
  if (body) {
    const isHidden = body.style.display === "none" || window.getComputedStyle(body).display === "none";
    body.style.display = isHidden ? "block" : "none";
    const indicator = header.querySelector('.collapse-indicator');
    if (indicator) {
      indicator.textContent = isHidden ? 'Collapse' : 'Expand';
    }
    header.setAttribute('aria-expanded', String(isHidden));
  }
}

// ===== Start =====
initialize();
