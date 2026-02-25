const btnBack = document.getElementById("btn-project-settings-back");
const btnSave = document.getElementById("btn-project-settings-save");
const messageEl = document.getElementById("project-settings-message");
const githubRepoUrlInput = document.getElementById("ps-github-repo-url");
const contextEl = document.getElementById("project-settings-context");
const headingEl = document.getElementById("project-settings-heading");

const state = {
  projectId: "",
  project: null,
  settings: {
    githubRepoUrl: ""
  }
};

function getParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    projectId: String(params.get("projectId") || "").trim()
  };
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
  state.settings = {
    githubRepoUrl: String(incoming.githubRepoUrl || "").trim()
  };
}

function populateForm() {
  if (githubRepoUrlInput) {
    githubRepoUrlInput.value = state.settings.githubRepoUrl;
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
  return {
    githubRepoUrl: String(githubRepoUrlInput?.value || "").trim()
  };
}

async function handleSave() {
  if (!state.project?.id) {
    setMessage("Unable to save: missing project context.", "error");
    return;
  }

  const settings = collectSettings();

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

  btnBack?.addEventListener("click", handleBack);
}

initialize().catch((error) => {
  setMessage(error.message || "Project settings initialization failed.", "error");
});
