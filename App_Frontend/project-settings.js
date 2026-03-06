const btnBack = document.getElementById("btn-project-settings-back");
const btnSave = document.getElementById("btn-project-settings-save");
const messageEl = document.getElementById("project-settings-message");
const githubRepoUrlInput = document.getElementById("ps-github-repo-url");
const projectThreadInput = document.getElementById("ps-project-thread");
const projectNameInput = document.getElementById("ps-project-name");
const projectIdInput = document.getElementById("ps-project-id");
const projectCloudInput = document.getElementById("ps-project-cloud");
const projectTypeInput = document.getElementById("ps-project-type");
const projectIacLanguageInputs = Array.from(document.querySelectorAll('input[name="ps-iac-language"]'));
const projectDescriptionInput = document.getElementById("ps-project-description");
const projectDescriptionQualityMeter = document.getElementById("ps-description-quality-meter");
const projectDescriptionQualityFill = document.getElementById("ps-description-quality-fill");
const projectDescriptionQualityStatus = document.getElementById("ps-description-quality-status");
const btnImproveDescription = document.getElementById("btn-ps-improve-description");
const contextEl = document.getElementById("project-settings-context");
const headingEl = document.getElementById("project-settings-heading");

const state = {
  projectId: "",
  project: null,
  settings: {
    githubRepoUrl: "",
    projectThreadId: "",
    iacLanguage: "bicep",
    projectDescription: "",
    projectDescriptionQuality: "",
    projectDescriptionQualityIndex: 0,
    projectDescriptionQualityScore: 0,
    projectApplicationType: ""
  },
  descriptionQuality: {
    index: 0,
    level: "Poor",
    score: 0
  },
  descriptionEvalToken: 0,
  descriptionEvalTimer: null,
  descriptionImproveToken: 0
};

const DESCRIPTION_LEVELS = ["Poor", "Minimal", "Adequate", "Informative", "Rich", "Perfect"];
const DESCRIPTION_EVAL_DELAY_MS = 4000;
const DEFAULT_IAC_LANGUAGE = "bicep";

function getParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    projectId: String(params.get("projectId") || "").trim()
  };
}

function ensureSelectOption(selectEl, value) {
  if (!selectEl) {
    return;
  }

  const optionValue = String(value || "").trim();
  if (!optionValue) {
    return;
  }

  const hasOption = Array.from(selectEl.options).some((option) => option.value === optionValue);
  if (hasOption) {
    return;
  }

  const optionEl = document.createElement("option");
  optionEl.value = optionValue;
  optionEl.textContent = optionValue;
  selectEl.appendChild(optionEl);
}

function setMessage(message, type = "") {
  if (!messageEl) {
    return;
  }

  messageEl.textContent = String(message || "");
  messageEl.classList.remove("is-error", "is-success", "is-info");
  if (type === "error") {
    messageEl.classList.add("is-error");
  } else if (type === "success") {
    messageEl.classList.add("is-success");
  } else if (type === "info") {
    messageEl.classList.add("is-info");
  }
}

function setQualityStatus(message, type = "") {
  if (!projectDescriptionQualityStatus) {
    return;
  }

  projectDescriptionQualityStatus.textContent = String(message || "");
  projectDescriptionQualityStatus.classList.remove("is-error", "is-info");
  if (type === "error") {
    projectDescriptionQualityStatus.classList.add("is-error");
  } else if (type === "info") {
    projectDescriptionQualityStatus.classList.add("is-info");
  }
}

function updateQualityMeter(levelIndex) {
  if (!projectDescriptionQualityMeter) {
    return;
  }

  const markers = Array.from(projectDescriptionQualityMeter.querySelectorAll(".quality-bar__marker"));
  const labels = Array.from(projectDescriptionQualityMeter.querySelectorAll(".quality-label"));
  const allItems = [...markers, ...labels];

  allItems.forEach((item) => {
    const itemLevel = Number(item.dataset.level);
    const level = Number.isFinite(itemLevel) ? itemLevel : 0;
    item.classList.toggle("is-active", level === levelIndex);
  });

  if (projectDescriptionQualityFill) {
    const maxIndex = Math.max(DESCRIPTION_LEVELS.length - 1, 1);
    const percent = Math.round((levelIndex / maxIndex) * 100);
    projectDescriptionQualityFill.style.width = `${percent}%`;
  }
}

function setDescriptionQuality(levelIndex, levelLabel, statusMessage = "", score = 0) {
  const safeIndex = Math.min(Math.max(Number(levelIndex) || 0, 0), DESCRIPTION_LEVELS.length - 1);
  const label = levelLabel || DESCRIPTION_LEVELS[safeIndex];
  const safeScore = Number.isFinite(Number(score)) ? Number(score) : 0;
  state.descriptionQuality = {
    index: safeIndex,
    level: label,
    score: safeScore
  };

  updateQualityMeter(safeIndex);
  if (statusMessage) {
    setQualityStatus(statusMessage, statusMessage.includes("failed") ? "error" : "");
  } else {
    setQualityStatus(`Quality: ${label}`);
  }
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

function scheduleDescriptionEvaluation() {
  if (!projectDescriptionInput || !state.projectId) {
    return;
  }

  const description = String(projectDescriptionInput.value || "").trim();
  if (state.descriptionEvalTimer) {
    window.clearTimeout(state.descriptionEvalTimer);
  }

  if (!description) {
    setDescriptionQuality(0, "Poor", "Quality: Not evaluated", 0);
    return;
  }

  setQualityStatus("Quality: Evaluating...", "info");
  state.descriptionEvalTimer = window.setTimeout(() => {
    evaluateDescription(description).catch(() => {
      setDescriptionQuality(0, "Poor", "Quality check failed", 0);
    });
  }, DESCRIPTION_EVAL_DELAY_MS);
}

async function evaluateDescription(description) {
  const token = ++state.descriptionEvalToken;
  const response = await fetch("/api/description/project/evaluate", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      projectId: state.projectId,
      description: String(description || "").trim(),
      appType: String(projectTypeInput?.value || "").trim(),
      cloud: String(state.project?.cloud || "").trim()
    })
  });

  if (token !== state.descriptionEvalToken) {
    return;
  }

  if (!response.ok) {
    throw new Error("Quality check failed");
  }

  const result = await response.json();
  if (!result?.ok || result?.skipped) {
    setDescriptionQuality(0, "Poor", "Quality check unavailable", 0);
    return;
  }

  setDescriptionQuality(result.levelIndex, result.level, `Quality: ${result.level}`, result.score);

  state.settings.projectDescription = String(description || "").trim();
  state.settings.projectDescriptionQuality = String(result.level || "").trim();
  state.settings.projectDescriptionQualityIndex = Number(result.levelIndex) || 0;
  state.settings.projectDescriptionQualityScore = Number(result.score) || 0;
  state.settings.projectApplicationType = String(projectTypeInput?.value || "").trim();
}

async function improveDescription() {
  if (!projectDescriptionInput || !btnImproveDescription) {
    return;
  }

  const rawDescription = String(projectDescriptionInput.value || "").trim();
  if (!rawDescription) {
    setMessage("Add a description first.", "error");
    return;
  }

  const token = ++state.descriptionImproveToken;
  btnImproveDescription.disabled = true;
  setQualityStatus("Improving description...", "info");

  try {
    const response = await fetch("/api/description/project/improve", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        projectId: state.projectId,
        description: rawDescription,
        appType: String(projectTypeInput?.value || "").trim(),
        cloud: String(state.project?.cloud || "").trim()
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

    projectDescriptionInput.value = improved;
    scheduleDescriptionEvaluation();
  } catch (error) {
    setQualityStatus("Quality: Improve failed", "error");
    setMessage(error.message || "Improve failed.", "error");
  } finally {
    btnImproveDescription.disabled = false;
  }
}

async function loadProjectDetails(projectId) {
  const response = await fetch(`/api/project/${encodeURIComponent(projectId)}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load project details.");
  }

  const payload = await response.json();
  const project = payload?.project && typeof payload.project === "object" ? payload.project : null;
  if (!project?.id) {
    throw new Error("Project details are incomplete.");
  }

  state.project = {
    id: String(project.id || ""),
    name: String(project.name || ""),
    cloud: String(project.cloud || ""),
    applicationType: String(project.applicationType || ""),
    applicationDescription: String(project.applicationDescription || "")
  };
}

async function loadProjectSettings(projectId) {
  const response = await fetch(`/api/settings/project/${encodeURIComponent(projectId)}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load project settings from file.");
  }

  const payload = await response.json();
  const incoming = payload?.settings && typeof payload.settings === "object" ? payload.settings : {};
  state.settings = {
    githubRepoUrl: String(incoming.githubRepoUrl || "").trim(),
    projectThreadId: String(incoming.projectThreadId || incoming.foundryThreadId || "").trim(),
    iacLanguage: String(incoming.iacLanguage || incoming.projectIacLanguage || "").trim(),
    projectDescription: String(incoming.projectDescription || "").trim(),
    projectDescriptionQuality: String(incoming.projectDescriptionQuality || "").trim(),
    projectDescriptionQualityIndex: Number(incoming.projectDescriptionQualityIndex) || 0,
    projectDescriptionQualityScore: Number(incoming.projectDescriptionQualityScore) || 0,
    projectApplicationType: String(incoming.projectApplicationType || "").trim()
  };
}

function populateForm() {
  if (githubRepoUrlInput) {
    githubRepoUrlInput.value = state.settings.githubRepoUrl;
  }

  if (projectThreadInput) {
    projectThreadInput.value = state.settings.projectThreadId;
  }

  if (projectNameInput) {
    projectNameInput.value = String(state.project?.name || "").trim();
  }
  if (projectIdInput) {
    projectIdInput.value = String(state.project?.id || "").trim();
  }
  if (projectCloudInput) {
    projectCloudInput.value = String(state.project?.cloud || "").trim();
  }
  if (projectTypeInput) {
    const nextType = String(
      state.settings.projectApplicationType
        || state.project?.applicationType
        || ""
    ).trim();
    ensureSelectOption(projectTypeInput, nextType);
    projectTypeInput.value = nextType;
  }
  setSelectedIacLanguage(
    projectIacLanguageInputs,
    state.settings.iacLanguage || DEFAULT_IAC_LANGUAGE,
  );
  if (projectDescriptionInput) {
    projectDescriptionInput.value = String(
      state.settings.projectDescription
        || state.project?.applicationDescription
        || ""
    ).trim();
  }

  const qualityIndex = Number(state.settings.projectDescriptionQualityIndex) || 0;
  const qualityLevel = String(state.settings.projectDescriptionQuality || "").trim() || DESCRIPTION_LEVELS[qualityIndex];
  const qualityScore = Number(state.settings.projectDescriptionQualityScore) || 0;
  if (projectDescriptionInput?.value) {
    setDescriptionQuality(qualityIndex, qualityLevel, `Quality: ${qualityLevel}`, qualityScore);
  } else {
    setDescriptionQuality(0, "Poor", "Quality: Not evaluated", 0);
  }

  const projectName = String(state.project?.name || "").trim();
  if (projectName) {
    if (contextEl) {
      contextEl.textContent = `(${projectName})`;
    }
    if (headingEl) {
      headingEl.textContent = `Project Settings - ${projectName}`;
    }
  }
}

function collectSettings() {
  const description = String(projectDescriptionInput?.value || "").trim();
  const applicationType = String(projectTypeInput?.value || "").trim();
  const quality = state.descriptionQuality || { index: 0, level: "", score: 0 };
  return {
    githubRepoUrl: String(githubRepoUrlInput?.value || "").trim(),
    projectThreadId: String(projectThreadInput?.value || "").trim(),
    iacLanguage: getSelectedIacLanguage(projectIacLanguageInputs),
    projectApplicationType: applicationType,
    projectDescription: description,
    projectDescriptionQuality: String(quality.level || "").trim(),
    projectDescriptionQualityIndex: Number(quality.index) || 0,
    projectDescriptionQualityScore: Number(quality.score) || 0
  };
}

async function handleSave() {
  if (!state.project?.id) {
    setMessage("Unable to save: missing project context.", "error");
    return;
  }

  const settings = collectSettings();
  if (state.project) {
    state.project = {
      ...state.project,
      applicationType: String(projectTypeInput?.value || "").trim(),
      applicationDescription: String(projectDescriptionInput?.value || "").trim()
    };
  }

  try {
    const response = await fetch("/api/settings/project", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        project: state.project,
        settings
      })
    });

    if (!response.ok) {
      throw new Error("Unable to write project settings file.");
    }

    state.settings = { ...settings };
    setMessage("project settings saved!", "success");
  } catch (error) {
    setMessage(error.message || "Failed to save project settings.", "error");
  }
}

function handleBack() {
  if (state.projectId) {
    window.location.href = `./canvas.html?projectId=${encodeURIComponent(state.projectId)}`;
    return;
  }

  window.location.href = "./landing.html";
}

async function initialize() {
  const params = getParams();
  state.projectId = params.projectId;

  if (!state.projectId) {
    setMessage("Project ID is missing. Open Project Settings from canvas.", "error");
    return;
  }

  try {
    await loadProjectDetails(state.projectId);
    await loadProjectSettings(state.projectId);
    populateForm();
    setMessage("Project settings loaded.", "info");
  } catch (error) {
    setMessage(error.message || "Unable to load project settings.", "error");
  }

  btnSave?.addEventListener("click", async () => {
    await handleSave();
  });

  projectDescriptionInput?.addEventListener("input", () => {
    scheduleDescriptionEvaluation();
  });

  projectTypeInput?.addEventListener("change", () => {
    scheduleDescriptionEvaluation();
  });

  btnImproveDescription?.addEventListener("click", () => {
    improveDescription();
  });

  btnBack?.addEventListener("click", handleBack);
}

initialize().catch((error) => {
  setMessage(error.message || "Project settings initialization failed.", "error");
});
