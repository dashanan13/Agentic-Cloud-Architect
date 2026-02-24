const btnBack = document.getElementById("btn-settings-back");
const btnVerify = document.getElementById("btn-settings-verify");
const btnSave = document.getElementById("btn-settings-save");
const settingsMessage = document.getElementById("settings-message");

const providerSelect = document.getElementById("as-model-provider");
const foundryFields = document.getElementById("as-foundry-fields");
const ollamaFields = document.getElementById("as-ollama-fields");
const foundryEndpointStatus = document.getElementById("as-foundry-endpoint-status");
const foundryKeyStatus = document.getElementById("as-foundry-api-key-status");
const ollamaCodingStatus = document.getElementById("as-ollama-model-coding-status");
const ollamaReasoningStatus = document.getElementById("as-ollama-model-reasoning-status");
const ollamaFastStatus = document.getElementById("as-ollama-model-fast-status");
const ollamaCodingSelect = document.getElementById("as-ollama-model-coding");
const ollamaReasoningSelect = document.getElementById("as-ollama-model-reasoning");
const ollamaFastSelect = document.getElementById("as-ollama-model-fast");

const DEFAULT_APP_SETTINGS = {
  modelProvider: "azure-foundry",
  foundryProjectRegion: "eastus2",
  foundryEndpoint: "",
  foundryApiKey: "",
  foundryApiVersion: "2024-05-01-preview",
  ollamaBaseUrl: "http://host.docker.internal:11434",
  foundryModelCoding: "gpt-5-codex",
  foundryModelReasoning: "o4-mini",
  foundryModelFast: "gpt-4o-mini",
  ollamaModelPathCoding: "",
  ollamaModelPathReasoning: "",
  ollamaModelPathFast: ""
};

const state = {
  appSettings: {},
  source: "landing",
  isVerified: false,
  ollamaModels: []
};

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
  foundryKeyStatus.classList.toggle("is-ok", showFoundry && isOk);

  ollamaCodingStatus.classList.toggle("is-ok", showOllama && isOk);
  ollamaReasoningStatus.classList.toggle("is-ok", showOllama && isOk);
  ollamaFastStatus.classList.toggle("is-ok", showOllama && isOk);
}

function resetVerification() {
  state.isVerified = false;
  btnSave.disabled = true;
  setStatusIcons(providerSelect.value, false);
}

function normalizeLegacyKeys(incoming) {
  const normalized = { ...(incoming || {}) };

  if (!normalized.foundryEndpoint && normalized.azureFoundryEndpoint) {
    normalized.foundryEndpoint = normalized.azureFoundryEndpoint;
  }
  if (!normalized.foundryApiKey && normalized.azureFoundryApiKey) {
    normalized.foundryApiKey = normalized.azureFoundryApiKey;
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
  document.getElementById("as-foundry-project-region").value = appSettings.foundryProjectRegion;
  document.getElementById("as-foundry-endpoint").value = appSettings.foundryEndpoint;
  document.getElementById("as-foundry-api-key").value = appSettings.foundryApiKey;
  document.getElementById("as-foundry-api-version").value = appSettings.foundryApiVersion;
  document.getElementById("as-ollama-base-url").value = appSettings.ollamaBaseUrl;
  document.getElementById("as-foundry-model-coding").value = appSettings.foundryModelCoding;
  document.getElementById("as-foundry-model-reasoning").value = appSettings.foundryModelReasoning;
  document.getElementById("as-foundry-model-fast").value = appSettings.foundryModelFast;

  setOllamaModelOptions(state.ollamaModels, {
    coding: appSettings.ollamaModelPathCoding,
    reasoning: appSettings.ollamaModelPathReasoning,
    fast: appSettings.ollamaModelPathFast
  });

  updateProviderVisibility();
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
  } else if (normalizedModels.length > 0) {
    selectElement.value = normalizedModels[0];
  } else {
    selectElement.value = "";
  }
}

function setOllamaModelOptions(models, preferred = {}) {
  setSelectOptions(ollamaCodingSelect, models, preferred.coding);
  setSelectOptions(ollamaReasoningSelect, models, preferred.reasoning);
  setSelectOptions(ollamaFastSelect, models, preferred.fast);
}

function collectAppSettings() {
  return {
    modelProvider: document.getElementById("as-model-provider").value,
    foundryProjectRegion: document.getElementById("as-foundry-project-region").value.trim(),
    foundryEndpoint: document.getElementById("as-foundry-endpoint").value.trim(),
    foundryApiKey: document.getElementById("as-foundry-api-key").value.trim(),
    foundryApiVersion: document.getElementById("as-foundry-api-version").value.trim(),
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
      setOllamaModelOptions(models, {
        coding: settings.ollamaModelPathCoding,
        reasoning: settings.ollamaModelPathReasoning,
        fast: settings.ollamaModelPathFast
      });
    }
    setStatusIcons(settings.modelProvider, true);
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
  btnSave.disabled = true;
  resetVerification();

  providerSelect.addEventListener("change", () => {
    updateProviderVisibility();
    resetVerification();
  });

  document.getElementById("app-settings-form").addEventListener("input", () => {
    resetVerification();
  });

  btnVerify.addEventListener("click", async () => {
    await handleVerify();
  });

  btnSave.addEventListener("click", async () => {
    await handleSave();
  });

  btnBack.addEventListener("click", handleBack);
}

initialize().catch((error) => {
  setMessage(error.message || "Settings initialization failed.", "error");
});
