const btnBack = document.getElementById("btn-settings-back");
const btnVerify = document.getElementById("btn-settings-verify");
const btnReset = document.getElementById("btn-settings-reset");
const btnSave = document.getElementById("btn-settings-save");
const settingsMessage = document.getElementById("settings-message");
const settingsPageTitle = document.getElementById("settings-page-title");
const settingsContext = document.getElementById("settings-context");
const settingsHeading = document.getElementById("settings-heading");
const settingsSubtitle = document.getElementById("settings-subtitle");
const appSettingsForm = document.getElementById("app-settings-form");
const projectSettingsForm = document.getElementById("project-settings-form");
const projectGithubRepoUrlInput = document.getElementById("ps-github-repo-url");

const providerSelect = document.getElementById("as-model-provider");
const foundryFields = document.getElementById("as-foundry-fields");
const ollamaFields = document.getElementById("as-ollama-fields");
const foundryEndpointStatus = document.getElementById("as-foundry-endpoint-status");
const ollamaBaseUrlStatus = document.getElementById("as-ollama-base-url-status");
const foundryCodingSelect = document.getElementById("as-foundry-model-coding");
const foundryReasoningSelect = document.getElementById("as-foundry-model-reasoning");
const foundryFastSelect = document.getElementById("as-foundry-model-fast");
const ollamaCodingSelect = document.getElementById("as-ollama-model-coding");
const ollamaReasoningSelect = document.getElementById("as-ollama-model-reasoning");
const ollamaFastSelect = document.getElementById("as-ollama-model-fast");

const DEFAULT_APP_SETTINGS = {
  modelProvider: "ollama-local",
  azureTenantId: "",
  azureClientId: "",
  azureClientSecret: "",
  azureSubscriptionId: "",
  azureResourceGroup: "",
  aiFoundryProjectName: "",
  aiFoundryEndpoint: "",
  foundryApiVersion: "2024-05-01-preview",
  ollamaBaseUrl: "http://host.docker.internal:11434",
  foundryModelCoding: "",
  foundryModelReasoning: "",
  foundryModelFast: "",
  ollamaModelPathCoding: "",
  ollamaModelPathReasoning: "",
  ollamaModelPathFast: ""
};

const DEFAULT_PROJECT_SETTINGS = {
  githubRepoUrl: ""
};

const state = {
  appSettings: {},
  projectSettings: {},
  currentProject: null,
  source: "landing",
  projectId: "",
  isProjectMode: false,
  isVerified: false,
  ollamaModels: [],
  foundryModels: []
};

const foundryModelFieldIds = new Set([
  "as-foundry-model-coding",
  "as-foundry-model-reasoning",
  "as-foundry-model-fast"
]);

const ollamaModelFieldIds = new Set([
  "as-ollama-model-coding",
  "as-ollama-model-reasoning",
  "as-ollama-model-fast"
]);

function getParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    source: params.get("source") || "landing",
    projectId: params.get("projectId"),
    section: params.get("section") || "application",
    mode: params.get("mode") || ""
  };
}

function isProjectContext(params) {
  return (
    params?.source === "canvas"
    || params?.section === "project"
    || params?.mode === "project-only"
  );
}

function applySettingsPageContext(params) {
  const projectMode = isProjectContext(params);

  if (projectMode) {
    if (settingsPageTitle) {
      settingsPageTitle.textContent = "Project Settings";
    }
    if (settingsContext) {
      settingsContext.textContent = "(Project)";
    }
    if (settingsHeading) {
      settingsHeading.textContent = "Project Settings";
    }
    if (settingsSubtitle) {
      settingsSubtitle.textContent = "Configure project-specific model settings for this canvas.";
    }
    if (btnSave) {
      btnSave.textContent = "Save Project Settings";
    }
    if (appSettingsForm) {
      appSettingsForm.classList.add("is-hidden");
      appSettingsForm.hidden = true;
    }
    if (projectSettingsForm) {
      projectSettingsForm.classList.remove("is-hidden");
      projectSettingsForm.hidden = false;
    }
    if (btnVerify) {
      btnVerify.classList.add("is-hidden");
      btnVerify.hidden = true;
    }
    if (btnReset) {
      btnReset.classList.add("is-hidden");
      btnReset.hidden = true;
    }
    if (btnSave) {
      btnSave.hidden = false;
      btnSave.disabled = false;
    }
    return;
  }

  if (settingsPageTitle) {
    settingsPageTitle.textContent = "Settings";
  }
  if (settingsContext) {
    settingsContext.textContent = "(Application)";
  }
  if (settingsHeading) {
    settingsHeading.textContent = "Application Settings";
  }
  if (settingsSubtitle) {
    settingsSubtitle.textContent = "Configure models for Azure AI Foundry or local Ollama.";
  }
  if (btnSave) {
    btnSave.textContent = "Save Application Settings";
  }
  if (appSettingsForm) {
    appSettingsForm.classList.remove("is-hidden");
    appSettingsForm.hidden = false;
  }
  if (projectSettingsForm) {
    projectSettingsForm.classList.add("is-hidden");
    projectSettingsForm.hidden = true;
  }
  if (btnVerify) {
    btnVerify.classList.remove("is-hidden");
    btnVerify.hidden = false;
  }
  if (btnReset) {
    btnReset.classList.remove("is-hidden");
    btnReset.hidden = false;
  }
}

function setMessage(message, type = "", secondaryMessage = "") {
  settingsMessage.replaceChildren();
  settingsMessage.classList.remove("is-error", "is-success", "is-info");

  const primaryLine = document.createElement("div");
  primaryLine.className = "form-message__line";
  primaryLine.textContent = String(message || "");
  settingsMessage.appendChild(primaryLine);

  if (secondaryMessage) {
    const secondaryLine = document.createElement("div");
    secondaryLine.className = "form-message__line form-message__line--secondary";
    secondaryLine.textContent = String(secondaryMessage);
    settingsMessage.appendChild(secondaryLine);
  }

  if (type === "error") {
    settingsMessage.classList.add("is-error");
  } else if (type === "success") {
    settingsMessage.classList.add("is-success");
  } else if (type === "info") {
    settingsMessage.classList.add("is-info");
  }
}

function applyStatusIcon(iconElement, isActive, status) {
  iconElement.classList.remove("is-ok", "is-error");
  iconElement.textContent = "";

  if (!isActive || status === "idle") {
    return;
  }

  if (status === "ok") {
    iconElement.classList.add("is-ok");
    iconElement.textContent = "OK";
    return;
  }

  iconElement.classList.add("is-error");
  iconElement.textContent = "Error";
}

function setStatusIcons(provider, status = "idle") {
  const showFoundry = provider === "azure-foundry";
  const showOllama = provider === "ollama-local";

  applyStatusIcon(foundryEndpointStatus, showFoundry, status);
  applyStatusIcon(ollamaBaseUrlStatus, showOllama, status);
}

function getFoundrySelectedModels() {
  return {
    coding: String(foundryCodingSelect?.value || "").trim(),
    reasoning: String(foundryReasoningSelect?.value || "").trim(),
    fast: String(foundryFastSelect?.value || "").trim()
  };
}

function getOllamaSelectedModels() {
  return {
    coding: String(ollamaCodingSelect?.value || "").trim(),
    reasoning: String(ollamaReasoningSelect?.value || "").trim(),
    fast: String(ollamaFastSelect?.value || "").trim()
  };
}

function resetVerification({ preserveSelections = true } = {}) {
  state.isVerified = false;
  state.foundryModels = [];
  state.ollamaModels = [];

  if (preserveSelections) {
    setFoundryModelOptions([], getFoundrySelectedModels());
    setOllamaModelOptions([], getOllamaSelectedModels());
  } else {
    setFoundryModelOptions([], {});
    setOllamaModelOptions([], {});
  }

  setFoundryModelLocked(true);
  setOllamaModelLocked(true);
  updateSaveButtonState();
  setStatusIcons("azure-foundry", "idle");
  setStatusIcons("ollama-local", "idle");
}

function setFoundryModelLocked(locked) {
  foundryCodingSelect.disabled = locked;
  foundryReasoningSelect.disabled = locked;
  foundryFastSelect.disabled = locked;
}

function setOllamaModelLocked(locked) {
  ollamaCodingSelect.disabled = locked;
  ollamaReasoningSelect.disabled = locked;
  ollamaFastSelect.disabled = locked;
}

function updateSaveButtonState() {
  const provider = providerSelect.value;
  let canSave = false;

  if (state.isVerified) {
    if (provider === "azure-foundry") {
      canSave = [foundryCodingSelect, foundryReasoningSelect, foundryFastSelect].every((field) => String(field.value || "").trim());
    } else {
      canSave = [ollamaCodingSelect, ollamaReasoningSelect, ollamaFastSelect].every((field) => String(field.value || "").trim());
    }
  }

  btnSave.hidden = !canSave;
  btnSave.disabled = !canSave;
}

function normalizeLegacyKeys(incoming) {
  const normalized = { ...(incoming || {}) };

  if (!normalized.foundryEndpoint && normalized.azureFoundryEndpoint) {
    normalized.foundryEndpoint = normalized.azureFoundryEndpoint;
  }
  if (!normalized.aiFoundryEndpoint && normalized.foundryEndpoint) {
    normalized.aiFoundryEndpoint = normalized.foundryEndpoint;
  }
  if (!normalized.aiFoundryEndpoint && normalized.azureFoundryEndpoint) {
    normalized.aiFoundryEndpoint = normalized.azureFoundryEndpoint;
  }
  if (!normalized.azureTenantId && normalized.foundryTenantId) {
    normalized.azureTenantId = normalized.foundryTenantId;
  }
  if (!normalized.azureClientId && normalized.foundryClientId) {
    normalized.azureClientId = normalized.foundryClientId;
  }
  if (!normalized.azureClientSecret && normalized.foundryClientSecret) {
    normalized.azureClientSecret = normalized.foundryClientSecret;
  }
  if (!normalized.foundryApiVersion && normalized.azureFoundryApiVersion) {
    normalized.foundryApiVersion = normalized.azureFoundryApiVersion;
  }
  if (!normalized.modelCoding && normalized.azureFoundryChatModelCoding) {
    normalized.modelCoding = normalized.azureFoundryChatModelCoding;
  }
  if (!normalized.modelReasoning && normalized.azureFoundryChatModelReasoning) {
    normalized.modelReasoning = normalized.azureFoundryChatModelReasoning;
  }
  if (!normalized.modelFast && normalized.azureFoundryChatModelFast) {
    normalized.modelFast = normalized.azureFoundryChatModelFast;
  }

  if (!normalized.foundryModelCoding && normalized.modelCoding) {
    normalized.foundryModelCoding = normalized.modelCoding;
  }
  if (!normalized.foundryModelReasoning && normalized.modelReasoning) {
    normalized.foundryModelReasoning = normalized.modelReasoning;
  }
  if (!normalized.foundryModelFast && normalized.modelFast) {
    normalized.foundryModelFast = normalized.modelFast;
  }

  return normalized;
}

async function loadAppSettings() {
  const response = await fetch("/api/settings/app", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load application settings from file.");
  }

  const payload = await response.json();
  const incoming = payload?.settings && typeof payload.settings === "object" ? payload.settings : {};
  const normalized = normalizeLegacyKeys(incoming);

  state.appSettings = {
    ...DEFAULT_APP_SETTINGS,
    ...normalized
  };
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

  state.currentProject = {
    id: String(project.id || ""),
    name: String(project.name || ""),
    cloud: String(project.cloud || "")
  };
}

async function loadProjectSettings(projectId) {
  const response = await fetch(`/api/settings/project/${encodeURIComponent(projectId)}`, { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load project settings from file.");
  }

  const payload = await response.json();
  const incoming = payload?.settings && typeof payload.settings === "object" ? payload.settings : {};
  state.projectSettings = {
    ...DEFAULT_PROJECT_SETTINGS,
    ...incoming
  };
}

function updateProviderVisibility() {
  const isFoundry = providerSelect.value === "azure-foundry";

  foundryFields.classList.toggle("is-hidden", !isFoundry);
  foundryFields.toggleAttribute("hidden", !isFoundry);

  ollamaFields.classList.toggle("is-hidden", isFoundry);
  ollamaFields.toggleAttribute("hidden", isFoundry);
}

function populateAppSettings() {
  const appSettings = state.appSettings;

  document.getElementById("as-model-provider").value = appSettings.modelProvider;
  document.getElementById("as-azure-tenant-id").value = appSettings.azureTenantId;
  document.getElementById("as-azure-client-id").value = appSettings.azureClientId;
  document.getElementById("as-azure-client-secret").value = appSettings.azureClientSecret;
  document.getElementById("as-azure-subscription-id").value = appSettings.azureSubscriptionId;
  document.getElementById("as-azure-resource-group").value = appSettings.azureResourceGroup;
  document.getElementById("as-ai-foundry-project-name").value = appSettings.aiFoundryProjectName;
  document.getElementById("as-ai-foundry-endpoint").value = appSettings.aiFoundryEndpoint;
  document.getElementById("as-ollama-base-url").value = appSettings.ollamaBaseUrl;

  setFoundryModelOptions([], {
    coding: appSettings.foundryModelCoding,
    reasoning: appSettings.foundryModelReasoning,
    fast: appSettings.foundryModelFast
  });
  setFoundryModelLocked(true);

  setOllamaModelOptions([], {
    coding: appSettings.ollamaModelPathCoding,
    reasoning: appSettings.ollamaModelPathReasoning,
    fast: appSettings.ollamaModelPathFast
  });
  setOllamaModelLocked(true);

  updateProviderVisibility();
  updateSaveButtonState();
}

function populateProjectSettings() {
  if (!projectGithubRepoUrlInput) {
    return;
  }

  projectGithubRepoUrlInput.value = String(state.projectSettings.githubRepoUrl || "");
}

function setSelectOptions(selectElement, models, preferredValue) {
  const normalizedModels = Array.from(new Set((models || []).map((item) => String(item || "").trim()).filter(Boolean))).sort();
  const preferred = String(preferredValue || "").trim();

  if (preferred && !normalizedModels.includes(preferred)) {
    normalizedModels.unshift(preferred);
  }

  selectElement.innerHTML = "";

  const emptyOption = document.createElement("option");
  emptyOption.value = "";
  emptyOption.textContent = "Select a model";
  selectElement.appendChild(emptyOption);

  normalizedModels.forEach((model) => {
    const option = document.createElement("option");
    option.value = model;
    option.textContent = model;
    selectElement.appendChild(option);
  });

  if (preferred) {
    selectElement.value = preferred;
  } else {
    selectElement.value = "";
  }
}

function setOllamaModelOptions(models, preferred = {}) {
  setSelectOptions(ollamaCodingSelect, models, preferred.coding);
  setSelectOptions(ollamaReasoningSelect, models, preferred.reasoning);
  setSelectOptions(ollamaFastSelect, models, preferred.fast);
}

function setFoundryModelOptions(models, preferred = {}) {
  setSelectOptions(foundryCodingSelect, models, preferred.coding);
  setSelectOptions(foundryReasoningSelect, models, preferred.reasoning);
  setSelectOptions(foundryFastSelect, models, preferred.fast);
}

async function resetFormState() {
  try {
    await loadAppSettings();
    state.foundryModels = [];
    state.ollamaModels = [];
    populateAppSettings();
    resetVerification();
    setMessage("Loaded saved settings from file.", "success");
  } catch (error) {
    setMessage(error.message || "Unable to reload saved settings.", "error");
  }
}

function buildProviderScopedSettings(settings) {
  const provider = String(settings?.modelProvider || "ollama-local").trim().toLowerCase();
  const scoped = {
    ...DEFAULT_APP_SETTINGS,
    ...settings,
    modelProvider: provider === "azure-foundry" ? "azure-foundry" : "ollama-local"
  };

  if (scoped.modelProvider === "azure-foundry") {
    scoped.ollamaBaseUrl = "";
    scoped.ollamaModelPathCoding = "";
    scoped.ollamaModelPathReasoning = "";
    scoped.ollamaModelPathFast = "";
  } else {
    scoped.azureSubscriptionId = "";
    scoped.azureResourceGroup = "";
    scoped.aiFoundryProjectName = "";
    scoped.aiFoundryEndpoint = "";
    scoped.foundryModelCoding = "";
    scoped.foundryModelReasoning = "";
    scoped.foundryModelFast = "";
  }

  return scoped;
}

function collectAppSettings() {
  return {
    modelProvider: document.getElementById("as-model-provider").value,
    azureTenantId: document.getElementById("as-azure-tenant-id").value.trim(),
    azureClientId: document.getElementById("as-azure-client-id").value.trim(),
    azureClientSecret: document.getElementById("as-azure-client-secret").value.trim(),
    azureSubscriptionId: document.getElementById("as-azure-subscription-id").value.trim(),
    azureResourceGroup: document.getElementById("as-azure-resource-group").value.trim(),
    aiFoundryProjectName: document.getElementById("as-ai-foundry-project-name").value.trim(),
    aiFoundryEndpoint: document.getElementById("as-ai-foundry-endpoint").value.trim(),
    foundryApiVersion: String(state.appSettings.foundryApiVersion || "").trim(),
    ollamaBaseUrl: document.getElementById("as-ollama-base-url").value.trim(),
    foundryModelCoding: document.getElementById("as-foundry-model-coding").value.trim(),
    foundryModelReasoning: document.getElementById("as-foundry-model-reasoning").value.trim(),
    foundryModelFast: document.getElementById("as-foundry-model-fast").value.trim(),
    ollamaModelPathCoding: document.getElementById("as-ollama-model-coding").value.trim(),
    ollamaModelPathReasoning: document.getElementById("as-ollama-model-reasoning").value.trim(),
    ollamaModelPathFast: document.getElementById("as-ollama-model-fast").value.trim()
  };
}

function collectProjectSettings() {
  return {
    githubRepoUrl: String(projectGithubRepoUrlInput?.value || "").trim()
  };
}

async function handleVerify() {
  const settings = collectAppSettings();
  resetVerification({ preserveSelections: false });
  setMessage("checking...", "info");
  setStatusIcons(settings.modelProvider, "idle");

  try {
    const response = await fetch("/api/settings/app/verify", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ settings })
    });

    const payload = await response.json().catch(() => ({}));

    if (!response.ok) {
      const detail = payload?.detail || "Verification failed.";
      throw new Error(detail);
    }

    state.isVerified = true;
    btnSave.disabled = false;
    if (settings.modelProvider === "ollama-local") {
      const models = Array.isArray(payload?.models) ? payload.models : [];
      state.ollamaModels = models;
      setOllamaModelOptions(models, {});
      setOllamaModelLocked(false);
    } else {
      const models = Array.isArray(payload?.models) ? payload.models : [];
      state.foundryModels = models;
      setFoundryModelOptions(models, {});
      setFoundryModelLocked(false);
    }
    setStatusIcons(settings.modelProvider, "ok");
    updateSaveButtonState();
    setMessage(payload?.message || "Verification succeeded.", "success", "Select models to enable Save.");
  } catch (error) {
    resetVerification({ preserveSelections: false });
    setStatusIcons(settings.modelProvider, "error");
    setMessage(error.message || "Verification failed.", "error");
  }
}

async function handleSave() {
  if (state.isProjectMode) {
    if (!state.currentProject?.id) {
      setMessage("Unable to save: project context is missing.", "error");
      return;
    }

    const settings = collectProjectSettings();

    try {
      const response = await fetch("/api/settings/project", {
        method: "POST",
        headers: {
          "Content-Type": "application/json"
        },
        body: JSON.stringify({
          project: state.currentProject,
          settings
        })
      });

      if (!response.ok) {
        throw new Error("Unable to write project settings file.");
      }

      state.projectSettings = {
        ...DEFAULT_PROJECT_SETTINGS,
        ...settings
      };

      setMessage("project settings saved!", "success");
    } catch (error) {
      setMessage(error.message || "Failed to save project settings.", "error");
    }
    return;
  }

  const settings = buildProviderScopedSettings(collectAppSettings());

  try {
    const response = await fetch("/api/settings/app", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({ settings })
    });

    if (!response.ok) {
      throw new Error("Unable to write application settings file.");
    }

    state.appSettings = {
      ...DEFAULT_APP_SETTINGS,
      ...settings
    };

    setMessage("settings saved!", "success");
  } catch (error) {
    setMessage(error.message || "Failed to save application settings.", "error");
  }
}

function handleBack() {
  const params = getParams();
  if (state.source === "canvas" && params.projectId) {
    window.location.href = `./canvas.html?projectId=${encodeURIComponent(params.projectId)}`;
    return;
  }

  window.location.href = "./landing.html";
}

async function initialize() {
  const params = getParams();
  state.source = params.source;
  state.projectId = String(params.projectId || "").trim();
  state.isProjectMode = isProjectContext(params);
  applySettingsPageContext(params);

  if (state.isProjectMode) {
    if (!state.projectId) {
      setMessage("Project ID is missing. Open Project Settings from canvas.", "error");
      return;
    }

    try {
      await loadProjectDetails(state.projectId);
      await loadProjectSettings(state.projectId);
      populateProjectSettings();
      setMessage("Project settings loaded.", "info");
    } catch (error) {
      state.projectSettings = { ...DEFAULT_PROJECT_SETTINGS };
      populateProjectSettings();
      setMessage(error.message || "Unable to load project settings.", "error");
    }

    projectSettingsForm?.addEventListener("input", () => {
      btnSave.hidden = false;
      btnSave.disabled = false;
    });

    btnSave.hidden = false;
    btnSave.disabled = false;
    btnSave.addEventListener("click", async () => {
      await handleSave();
    });
    btnBack.addEventListener("click", handleBack);
    return;
  }

  try {
    await loadAppSettings();
  } catch (error) {
    state.appSettings = { ...DEFAULT_APP_SETTINGS };
    setMessage(error.message || "Unable to load app settings.", "error");
  }

  populateAppSettings();
  btnSave.hidden = true;
  btnSave.disabled = true;
  resetVerification();
  setMessage("Fill values and verify.", "info");

  providerSelect.addEventListener("change", () => {
    updateProviderVisibility();
    resetVerification();
  });

  document.getElementById("app-settings-form").addEventListener("input", (event) => {
    const targetId = event?.target?.id || "";
    if (foundryModelFieldIds.has(targetId) || ollamaModelFieldIds.has(targetId)) {
      updateSaveButtonState();
      return;
    }
    resetVerification();
  });

  document.getElementById("app-settings-form").addEventListener("change", (event) => {
    const targetId = event?.target?.id || "";
    if (foundryModelFieldIds.has(targetId) || ollamaModelFieldIds.has(targetId)) {
      updateSaveButtonState();
      return;
    }
    resetVerification();
  });

  btnVerify.addEventListener("click", async () => {
    await handleVerify();
  });

  btnSave.addEventListener("click", async () => {
    await handleSave();
  });

  btnReset.addEventListener("click", async () => {
    await resetFormState();
  });

  btnBack.addEventListener("click", handleBack);
}

initialize().catch((error) => {
  setMessage(error.message || "Settings initialization failed.", "error");
});
