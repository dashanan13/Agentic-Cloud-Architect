const btnBack = document.getElementById("btn-settings-back");
const btnSave = document.getElementById("btn-settings-save");
const settingsMessage = document.getElementById("settings-message");
const settingsContext = document.getElementById("settings-context");

const tabProject = document.getElementById("settings-tab-project");
const tabApp = document.getElementById("settings-tab-app");
const panelProject = document.getElementById("settings-project-panel");
const panelApp = document.getElementById("settings-app-panel");

const projectForm = document.getElementById("project-settings-form");
const appForm = document.getElementById("app-settings-form");
const projectEmpty = document.getElementById("settings-project-empty");

const state = {
  projects: [],
  currentProject: null,
  appSettings: {},
  currentProjectSettings: {},
  activeTab: "project",
  source: "landing",
  mode: "full"
};

const DEFAULT_APP_SETTINGS = {
  defaultRegion: "eastus",
  defaultIacEngine: "Bicep",
  storageRoot: "Projects/Default",
  mcpTimeoutSeconds: "30",
  autoSaveSeconds: "15",
  theme: "light",
  telemetry: "enabled"
};

const DEFAULT_PROJECT_SETTINGS = {
  gitRepoUrl: "",
  gitBranch: "main",
  gitAuthMode: "pat",
  identityType: "managed-identity",
  azureSubscriptionId: "",
  azureTenantId: "",
  azureMcpEndpoint: "",
  llmProvider: "azure-openai",
  llmModelName: "gpt-4o",
  artifactsPath: "Projects/Default"
};

function getParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    projectId: params.get("projectId"),
    section: params.get("section") || "project",
    source: params.get("source") || "landing",
    mode: params.get("mode") || "full"
  };
}

function setMessage(message, type = "") {
  settingsMessage.textContent = message;
  settingsMessage.classList.remove("is-error", "is-success");
  if (type) {
    settingsMessage.classList.add(type === "error" ? "is-error" : "is-success");
  }
}

async function loadProjects() {
  const response = await fetch("/api/projects", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load projects from files.");
  }

  const payload = await response.json();
  state.projects = Array.isArray(payload?.projects) ? payload.projects : [];
}

async function loadAppSettings() {
  const response = await fetch("/api/settings/app", { cache: "no-store" });
  if (!response.ok) {
    throw new Error("Unable to load application settings from file.");
  }

  const payload = await response.json();
  const incoming = payload?.settings && typeof payload.settings === "object" ? payload.settings : {};
  state.appSettings = {
    ...DEFAULT_APP_SETTINGS,
    ...incoming
  };
}

async function loadProjectSettings(projectId) {
  if (!projectId) {
    state.currentProjectSettings = { ...DEFAULT_PROJECT_SETTINGS };
    return;
  }

  const response = await fetch(`/api/settings/project/${encodeURIComponent(projectId)}`, { cache: "no-store" });
  if (response.status === 404) {
    state.currentProjectSettings = { ...DEFAULT_PROJECT_SETTINGS };
    return;
  }

  if (!response.ok) {
    throw new Error("Unable to load project settings from file.");
  }

  const payload = await response.json();
  const incoming = payload?.settings && typeof payload.settings === "object" ? payload.settings : {};
  state.currentProjectSettings = {
    ...DEFAULT_PROJECT_SETTINGS,
    ...incoming
  };
}

function setTab(tabName) {
  if (state.mode === "app-only") {
    tabName = "app";
  }
  if (state.mode === "project-only") {
    tabName = "project";
  }

  state.activeTab = tabName;
  const isProject = tabName === "project";

  tabProject.classList.toggle("is-active", isProject);
  tabProject.setAttribute("aria-selected", String(isProject));

  tabApp.classList.toggle("is-active", !isProject);
  tabApp.setAttribute("aria-selected", String(!isProject));

  panelProject.classList.toggle("is-hidden", !isProject);
  panelProject.toggleAttribute("hidden", !isProject);

  panelApp.classList.toggle("is-hidden", isProject);
  panelApp.toggleAttribute("hidden", isProject);
}

function bindTabHandlers() {
  if (state.mode === "app-only" || state.mode === "project-only") {
    return;
  }

  tabProject.addEventListener("click", () => setTab("project"));
  tabApp.addEventListener("click", () => setTab("app"));
}

function applyModeVisibility() {
  if (state.mode === "app-only") {
    tabProject.classList.add("is-hidden");
    tabProject.setAttribute("hidden", "");
    tabApp.classList.add("is-active");
    settingsContext.textContent = "(Application scope)";
  }

  if (state.mode === "project-only") {
    tabApp.classList.add("is-hidden");
    tabApp.setAttribute("hidden", "");
    tabProject.classList.add("is-active");
  }
}

function populateAppSettings() {
  const appSettings = state.appSettings;

  document.getElementById("as-default-region").value = appSettings.defaultRegion;
  document.getElementById("as-default-engine").value = appSettings.defaultIacEngine;
  document.getElementById("as-storage-root").value = appSettings.storageRoot;
  document.getElementById("as-timeout").value = appSettings.mcpTimeoutSeconds;
  document.getElementById("as-autosave").value = appSettings.autoSaveSeconds;
  document.getElementById("as-theme").value = appSettings.theme;
  document.getElementById("as-telemetry").value = appSettings.telemetry;
}

async function populateProjectSettings(projectId) {
  if (!projectId) {
    projectForm.classList.add("is-hidden");
    projectForm.setAttribute("hidden", "");
    projectEmpty.classList.remove("is-hidden");
    projectEmpty.removeAttribute("hidden");
    settingsContext.textContent = "(Application scope)";
    return;
  }

  const project = state.projects.find((item) => item.id === projectId);
  if (!project) {
    projectForm.classList.add("is-hidden");
    projectForm.setAttribute("hidden", "");
    projectEmpty.classList.remove("is-hidden");
    projectEmpty.textContent = "Project not found. Open settings from a valid project.";
    settingsContext.textContent = "(Project unavailable)";
    return;
  }

  state.currentProject = project;
  settingsContext.textContent = `(${project.name})`;

  projectForm.classList.remove("is-hidden");
  projectForm.removeAttribute("hidden");
  projectEmpty.classList.add("is-hidden");
  projectEmpty.setAttribute("hidden", "");

  await loadProjectSettings(projectId);
  const projectSettings = state.currentProjectSettings;

  document.getElementById("ps-git-repo").value = projectSettings.gitRepoUrl;
  document.getElementById("ps-git-branch").value = projectSettings.gitBranch;
  document.getElementById("ps-git-auth").value = projectSettings.gitAuthMode;
  document.getElementById("ps-identity").value = projectSettings.identityType;
  document.getElementById("ps-subscription").value = projectSettings.azureSubscriptionId;
  document.getElementById("ps-tenant").value = projectSettings.azureTenantId;
  document.getElementById("ps-mcp-endpoint").value = projectSettings.azureMcpEndpoint;
  document.getElementById("ps-model-provider").value = projectSettings.llmProvider;
  document.getElementById("ps-model-name").value = projectSettings.llmModelName;
  document.getElementById("ps-artifacts-path").value = projectSettings.artifactsPath;
}

function collectAppSettings() {
  return {
    defaultRegion: document.getElementById("as-default-region").value.trim(),
    defaultIacEngine: document.getElementById("as-default-engine").value,
    storageRoot: document.getElementById("as-storage-root").value.trim(),
    mcpTimeoutSeconds: document.getElementById("as-timeout").value.trim(),
    autoSaveSeconds: document.getElementById("as-autosave").value.trim(),
    theme: document.getElementById("as-theme").value,
    telemetry: document.getElementById("as-telemetry").value
  };
}

function collectProjectSettings() {
  return {
    gitRepoUrl: document.getElementById("ps-git-repo").value.trim(),
    gitBranch: document.getElementById("ps-git-branch").value.trim(),
    gitAuthMode: document.getElementById("ps-git-auth").value,
    identityType: document.getElementById("ps-identity").value,
    azureSubscriptionId: document.getElementById("ps-subscription").value.trim(),
    azureTenantId: document.getElementById("ps-tenant").value.trim(),
    azureMcpEndpoint: document.getElementById("ps-mcp-endpoint").value.trim(),
    llmProvider: document.getElementById("ps-model-provider").value,
    llmModelName: document.getElementById("ps-model-name").value.trim(),
    artifactsPath: document.getElementById("ps-artifacts-path").value.trim()
  };
}

async function saveSettingsToFile(appSettings, projectSettings) {
  const appResponse = await fetch("/api/settings/app", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({ settings: appSettings })
  });

  if (!appResponse.ok) {
    throw new Error("Unable to write application settings file.");
  }

  if (!state.currentProject || !projectSettings) {
    return;
  }

  const projectResponse = await fetch("/api/settings/project", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      project: {
        id: state.currentProject.id,
        name: state.currentProject.name,
        cloud: state.currentProject.cloud
      },
      settings: projectSettings
    })
  });

  if (!projectResponse.ok) {
    throw new Error("Unable to write project settings file.");
  }
}

async function handleSave() {
  const appSettings = collectAppSettings();

  let projectSettings = null;

  if (state.currentProject) {
    projectSettings = collectProjectSettings();
  }

  try {
    await saveSettingsToFile(appSettings, projectSettings);
    state.appSettings = { ...DEFAULT_APP_SETTINGS, ...appSettings };
    state.currentProjectSettings = {
      ...DEFAULT_PROJECT_SETTINGS,
      ...(projectSettings || state.currentProjectSettings)
    };
    setMessage("Settings saved to .env files.", "success");
  } catch (error) {
    setMessage(error.message || "Failed to save settings files.", "error");
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
  const { projectId, section, source, mode } = getParams();
  state.source = source;
  state.mode = mode;

  try {
    await loadProjects();
  } catch (error) {
    state.projects = [];
    setMessage(error.message || "Unable to load projects.", "error");
  }

  try {
    await loadAppSettings();
  } catch (error) {
    state.appSettings = { ...DEFAULT_APP_SETTINGS };
    setMessage(error.message || "Unable to load app settings.", "error");
  }

  populateAppSettings();
  await populateProjectSettings(projectId);

  applyModeVisibility();
  bindTabHandlers();
  setTab(section === "app" ? "app" : "project");

  btnSave.addEventListener("click", async () => {
    await handleSave();
  });
  btnBack.addEventListener("click", handleBack);
}

initialize().catch((error) => {
  setMessage(error.message || "Settings initialization failed.", "error");
});
