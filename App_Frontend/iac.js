const btnBack = document.getElementById("btn-iac-back");
const btnRefresh = document.getElementById("btn-iac-refresh");
const btnCopy = document.getElementById("btn-iac-copy");
const fileListEl = document.getElementById("iac-file-list");
const fileCountEl = document.getElementById("iac-file-count");
const fileContentEl = document.getElementById("iac-file-content");
const viewerTitleEl = document.getElementById("iac-viewer-title");
const contextEl = document.getElementById("iac-project-context");
const statusEl = document.getElementById("iac-status");
const statusTextEl = document.getElementById("iac-status-text");

const state = {
  projectId: "",
  project: null,
  files: [],
  selectedPath: "",
  statusMeta: ""
};

function getParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    projectId: String(params.get("projectId") || "").trim()
  };
}

function setStatus(status, { text } = {}) {
  if (statusEl) {
    statusEl.classList.remove("is-idle", "is-running", "is-complete", "is-error");
    if (status) {
      statusEl.classList.add(`is-${status}`);
    }
  }

  if (statusTextEl && text) {
    statusTextEl.textContent = text;
  }
}

function setFileContent(message, isEmpty = false) {
  if (!fileContentEl) {
    return;
  }

  fileContentEl.textContent = String(message || "");
  fileContentEl.classList.toggle("is-empty", Boolean(isEmpty));
  if (btnCopy) {
    btnCopy.disabled = Boolean(isEmpty) || !fileContentEl.textContent.trim();
  }
}

function updateViewerTitle(path) {
  if (!viewerTitleEl) {
    return;
  }

  if (!path) {
    viewerTitleEl.textContent = "File Preview";
    return;
  }

  viewerTitleEl.textContent = `File Preview - ${path}`;
}

function renderFileList() {
  if (!fileListEl) {
    return;
  }

  fileListEl.innerHTML = "";

  if (!state.files.length) {
    const empty = document.createElement("div");
    empty.className = "iac-file-empty";
    empty.textContent = "No IaC files found yet.";
    fileListEl.appendChild(empty);
    return;
  }

  state.files.forEach((file) => {
    const button = document.createElement("button");
    button.type = "button";
    button.className = "iac-file-item";
    button.dataset.path = file.path;
    if (file.path === state.selectedPath) {
      button.classList.add("is-active");
    }

    const nameEl = document.createElement("div");
    nameEl.className = "iac-file-item__name";
    nameEl.textContent = file.name || file.path;

    const pathEl = document.createElement("div");
    pathEl.className = "iac-file-item__path";
    pathEl.textContent = file.path;

    button.appendChild(nameEl);
    button.appendChild(pathEl);

    button.addEventListener("click", () => {
      selectFile(file.path);
    });

    fileListEl.appendChild(button);
  });
}

async function loadProject(projectId) {
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

  if (contextEl) {
    const projectName = String(state.project.name || "").trim() || "Project";
    contextEl.textContent = `(${projectName})`;
  }
}

async function loadFiles() {
  if (!state.projectId) {
    return;
  }

  setStatus("running", {
    text: "IaC status: validating output"
  });

  try {
    const response = await fetch(`/api/project/${encodeURIComponent(state.projectId)}/iac/files`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error("Unable to load IaC files.");
    }

    const payload = await response.json();
    const files = Array.isArray(payload?.files) ? payload.files : [];
    state.files = files
      .map((file) => ({
        path: String(file.path || "").trim(),
        name: String(file.name || "").trim(),
        size: Number(file.size) || 0,
        updated: Number(file.updated) || 0
      }))
      .filter((file) => file.path);

    if (fileCountEl) {
      const label = state.files.length === 1 ? "file" : "files";
      fileCountEl.textContent = `${state.files.length} ${label}`;
    }

    if (state.files.length) {
      const label = state.files.length === 1 ? "file" : "files";
      setStatus("complete", {
        text: `IaC status: planned for ${state.files.length} ${label}`
      });

      const hasSelection = state.files.some((file) => file.path === state.selectedPath);
      if (!state.selectedPath || !hasSelection) {
        selectFile(state.files[0].path);
      } else {
        renderFileList();
        loadFileContent(state.selectedPath);
      }
      return;
    }

    state.selectedPath = "";
    renderFileList();
    updateViewerTitle("");
    setFileContent("No IaC files available yet. Generate code to see results here.", true);
    setStatus("idle", {
      text: "IaC status: waiting"
    });
  } catch (error) {
    state.files = [];
    state.selectedPath = "";
    renderFileList();
    updateViewerTitle("");
    setFileContent("Failed to load IaC files.", true);
    setStatus("error", {
      text: `IaC status: error - ${error.message || "unable to load"}`
    });
  }
}

async function loadFileContent(path) {
  if (!state.projectId || !path) {
    return;
  }

  updateViewerTitle(path);
  setFileContent("Loading file...", true);

  try {
    const response = await fetch(
      `/api/project/${encodeURIComponent(state.projectId)}/iac/file?path=${encodeURIComponent(path)}`,
      { cache: "no-store" }
    );
    if (!response.ok) {
      throw new Error("Unable to load file contents.");
    }

    const payload = await response.json();
    const content = String(payload?.content || "");
    setFileContent(content || "Empty file.", !content.trim());
  } catch (error) {
    setFileContent(error.message || "Unable to load file contents.", true);
  }
}

function selectFile(path) {
  if (!path || path === state.selectedPath) {
    return;
  }

  state.selectedPath = path;
  renderFileList();
  loadFileContent(path);
}

function handleBack() {
  if (state.projectId) {
    window.location.href = `./canvas.html?projectId=${encodeURIComponent(state.projectId)}`;
    return;
  }

  window.location.href = "./landing.html";
}

async function handleCopy() {
  if (!fileContentEl || !fileContentEl.textContent.trim()) {
    return;
  }

  try {
    await navigator.clipboard.writeText(fileContentEl.textContent);
    if (statusTextEl) {
      const original = statusTextEl.textContent;
      statusTextEl.textContent = "IaC status: copied to clipboard";
      window.setTimeout(() => {
        if (statusTextEl) {
          statusTextEl.textContent = original;
        }
      }, 1600);
    }
  } catch {
    if (statusTextEl) {
      statusTextEl.textContent = "IaC status: copy failed";
    }
  }
}

async function initialize() {
  const params = getParams();
  state.projectId = params.projectId;

  btnBack?.addEventListener("click", handleBack);
  btnRefresh?.addEventListener("click", () => {
    loadFiles();
  });
  btnCopy?.addEventListener("click", () => {
    handleCopy();
  });

  if (!state.projectId) {
    setStatus("error", {
      text: "IaC status: missing project context"
    });
    setFileContent("Project ID is missing. Return to the canvas and try again.", true);
    renderFileList();
    return;
  }

  try {
    await loadProject(state.projectId);
  } catch (error) {
    setStatus("error", {
      text: `IaC status: error - ${error.message || "unable to load project"}`
    });
    setFileContent("Project details could not be loaded.", true);
    renderFileList();
    return;
  }

  await loadFiles();
}

initialize().catch(() => {
  setStatus("error", {
    text: "IaC status: page failed to load"
  });
  setFileContent("Initialization failed.", true);
});
