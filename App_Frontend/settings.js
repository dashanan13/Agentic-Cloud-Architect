const btnBack = document.getElementById("btn-settings-back");
const btnVerify = document.getElementById("btn-settings-verify");
const btnReset = document.getElementById("btn-settings-reset");
const btnSave = document.getElementById("btn-settings-save");
const settingsMessage = document.getElementById("settings-message");

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
  modelProvider: "azure-foundry",
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

const state = {
  appSettings: {},
  source: "landing",
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
    projectId: params.get("projectId")
  };
}

function setMessage(message, type = "") {
  settingsMessage.textContent = message;
  settingsMessage.classList.remove("is-error", "is-success");
  if (type) {
    settingsMessage.classList.add(type === "error" ? "is-error" : "is-success");
  }
}

function setStatusIcons(provider, isOk) {
  const showFoundry = provider === "azure-foundry";
  const showOllama = provider === "ollama-local";

  foundryEndpointStatus.classList.toggle("is-ok", showFoundry && isOk);
  ollamaBaseUrlStatus.classList.toggle("is-ok", showOllama && isOk);
}

function resetVerification() {
  state.isVerified = false;
  if (providerSelect.value === "azure-foundry") {
    state.foundryModels = [];
    setFoundryModelOptions([], {});
    setFoundryModelLocked(true);
  } else {
    state.ollamaModels = [];
    setOllamaModelOptions([], {});
    setOllamaModelLocked(true);
  }
  updateSaveButtonState();
  setStatusIcons(providerSelect.value, false);
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

  setFoundryModelOptions([], {});
  setFoundryModelLocked(true);

  setOllamaModelOptions([], {});
  setOllamaModelLocked(true);

  updateProviderVisibility();
  updateSaveButtonState();
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

function resetFormState() {
  const currentSettings = collectAppSettings();
  const provider = providerSelect.value;

  if (provider === "azure-foundry") {
    currentSettings.azureTenantId = DEFAULT_APP_SETTINGS.azureTenantId;
    currentSettings.azureClientId = DEFAULT_APP_SETTINGS.azureClientId;
    currentSettings.azureClientSecret = "";
    currentSettings.azureSubscriptionId = DEFAULT_APP_SETTINGS.azureSubscriptionId;
    currentSettings.azureResourceGroup = DEFAULT_APP_SETTINGS.azureResourceGroup;
    currentSettings.aiFoundryProjectName = DEFAULT_APP_SETTINGS.aiFoundryProjectName;
    currentSettings.aiFoundryEndpoint = DEFAULT_APP_SETTINGS.aiFoundryEndpoint;
    currentSettings.foundryModelCoding = "";
    currentSettings.foundryModelReasoning = "";
    currentSettings.foundryModelFast = "";
    state.foundryModels = [];
  } else {
    currentSettings.ollamaBaseUrl = DEFAULT_APP_SETTINGS.ollamaBaseUrl;
    currentSettings.ollamaModelPathCoding = "";
    currentSettings.ollamaModelPathReasoning = "";
    currentSettings.ollamaModelPathFast = "";
    state.ollamaModels = [];
  }

  state.appSettings = {
    ...state.appSettings,
    ...currentSettings
  };

  populateAppSettings();
  resetVerification();
  setMessage(`${provider === "azure-foundry" ? "Foundry" : "Ollama"} fields reset.`);
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

async function handleVerify() {
  const settings = collectAppSettings();

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
    setStatusIcons(settings.modelProvider, true);
    updateSaveButtonState();
    setMessage(payload?.message || "Verification succeeded.", "success");
  } catch (error) {
    resetVerification();
    setMessage(error.message || "Verification failed.", "error");
  }
}

async function handleSave() {
  const settings = collectAppSettings();

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

    setMessage("Application settings saved.", "success");
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

  btnReset.addEventListener("click", () => {
    resetFormState();
  });

  btnBack.addEventListener("click", handleBack);
}

initialize().catch((error) => {
  setMessage(error.message || "Settings initialization failed.", "error");
});
