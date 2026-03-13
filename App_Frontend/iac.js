const btnBack = document.getElementById("btn-iac-back");
const btnCopy = document.getElementById("btn-iac-copy");
const fileListEl = document.getElementById("iac-file-list");
const fileCountEl = document.getElementById("iac-file-count");
const fileContentEl = document.getElementById("iac-file-content");
const viewerTitleEl = document.getElementById("iac-viewer-title");
const contextEl = document.getElementById("iac-project-context");
const statusEl = document.getElementById("iac-status");
const statusTextEl = document.getElementById("iac-status-text");
const statusMetaEl = document.getElementById("iac-status-meta");
const progressFillEl = document.getElementById("iac-progress-fill");
const progressMilestonesEl = document.getElementById("iac-progress-milestones");
const stageListEl = document.getElementById("iac-stage-list");

const STAGE_TEMPLATE = [
  { id: "gather_properties", label: "Gather resource properties" },
  { id: "dependency_tree", label: "Build dependency tree" },
  { id: "render_templates", label: "Render Bicep templates" },
  { id: "generate_parameters", label: "Generate parameters and pipeline" },
  { id: "guardrails_mcp", label: "Run MCP guardrails" },
  { id: "guardrails_model", label: "Run coding-model guardrails" },
  { id: "write_files", label: "Write generated files" }
];
const AUTOSTART_INTENT_TTL_MS = 10 * 60 * 1000;

const state = {
  projectId: "",
  project: null,
  files: [],
  selectedPath: "",
  taskId: "",
  parameterFormat: "",
  autostart: false,
  pollTimer: null,
  latestTask: null,
  loadedTaskResultId: "",
  collapsedDirs: new Set()
};

function consumeAutostartIntent(projectId) {
  const safeProjectId = String(projectId || "").trim();
  if (!safeProjectId) {
    return { autostart: false, parameterFormat: "" };
  }

  const key = `iac-autostart:${safeProjectId}`;
  try {
    const raw = sessionStorage.getItem(key);
    if (!raw) {
      return { autostart: false, parameterFormat: "" };
    }
    sessionStorage.removeItem(key);

    const payload = JSON.parse(raw);
    const createdAt = Number(payload?.createdAt || 0);
    if (createdAt > 0 && Date.now() - createdAt > AUTOSTART_INTENT_TTL_MS) {
      return { autostart: false, parameterFormat: "" };
    }

    const parameterFormat = String(payload?.parameterFormat || "").trim().toLowerCase() === "json"
      ? "json"
      : "bicepparam";

    return {
      autostart: true,
      parameterFormat,
    };
  } catch {
    try {
      sessionStorage.removeItem(key);
    } catch {
    }
    return { autostart: false, parameterFormat: "" };
  }
}

function syncTaskUrl(taskId = "") {
  const current = new URL(window.location.href);
  if (taskId) {
    current.searchParams.set("taskId", taskId);
  } else {
    current.searchParams.delete("taskId");
  }
  current.searchParams.delete("autostart");
  const nextQuery = current.searchParams.toString();
  const nextUrl = `${current.pathname}${nextQuery ? `?${nextQuery}` : ""}`;
  window.history.replaceState({}, "", nextUrl);
}

function getParams() {
  const params = new URLSearchParams(window.location.search);
  return {
    projectId: String(params.get("projectId") || "").trim(),
    taskId: String(params.get("taskId") || "").trim(),
    autostart: String(params.get("autostart") || "").trim() === "1",
    parameterFormat: String(params.get("parameterFormat") || "").trim().toLowerCase()
  };
}

function clampProgress(value, fallback = 0) {
  const parsed = Number.parseInt(String(value ?? fallback), 10);
  if (!Number.isFinite(parsed)) {
    return fallback;
  }
  if (parsed < 0) {
    return 0;
  }
  if (parsed > 100) {
    return 100;
  }
  return parsed;
}

function buildDefaultStages() {
  return STAGE_TEMPLATE.map((stage) => ({
    id: stage.id,
    label: stage.label,
    status: "pending",
    message: "",
    startedAt: null,
    completedAt: null
  }));
}

function normalizeTask(task) {
  if (!task || typeof task !== "object") {
    return null;
  }

  const taskId = String(task.taskId || "").trim();
  if (!taskId) {
    return null;
  }

  const status = String(task.status || "queued").trim().toLowerCase();
  const stages = Array.isArray(task.stages)
    ? task.stages
      .map((stage) => ({
        id: String(stage?.id || "").trim(),
        label: String(stage?.label || "").trim(),
        status: String(stage?.status || "pending").trim().toLowerCase(),
        message: String(stage?.message || "").trim(),
        startedAt: stage?.startedAt ?? null,
        completedAt: stage?.completedAt ?? null
      }))
      .filter((stage) => stage.id)
    : [];

  const defaultStages = buildDefaultStages();
  const stageMap = new Map(stages.map((stage) => [stage.id, stage]));
  const normalizedStages = defaultStages.map((template) => {
    const existing = stageMap.get(template.id);
    if (!existing) {
      return template;
    }
    return {
      ...template,
      label: existing.label || template.label,
      status: existing.status || template.status,
      message: existing.message || "",
      startedAt: existing.startedAt,
      completedAt: existing.completedAt
    };
  });

  return {
    taskId,
    status,
    message: String(task.message || "").trim(),
    error: String(task.error || "").trim(),
    progress: clampProgress(task.progress, 0),
    stages: normalizedStages
  };
}

function setStatus(status, { text, meta, progress } = {}) {
  if (statusEl) {
    statusEl.classList.remove("is-idle", "is-running", "is-complete", "is-error");
    const normalized = status || "idle";
    statusEl.classList.add(`is-${normalized}`);
  }

  if (statusTextEl && typeof text === "string" && text.trim()) {
    statusTextEl.textContent = text;
  }

  if (statusMetaEl && typeof meta === "string") {
    statusMetaEl.textContent = meta;
  }

  if (progressFillEl && progress !== undefined) {
    progressFillEl.style.width = `${clampProgress(progress, 0)}%`;
  }
}

function renderProgressMilestones(stages) {
  if (!progressMilestonesEl) {
    return;
  }

  const safeStages = Array.isArray(stages) && stages.length ? stages : buildDefaultStages();
  progressMilestonesEl.innerHTML = "";

  const denominator = Math.max(safeStages.length - 1, 1);
  safeStages.forEach((stage, index) => {
    const marker = document.createElement("span");
    marker.className = "iac-progress__milestone";

    const status = String(stage?.status || "pending").trim().toLowerCase();
    if (status === "running") {
      marker.classList.add("is-running");
    } else if (status === "completed") {
      marker.classList.add("is-completed");
    } else if (status === "error") {
      marker.classList.add("is-error");
    }

    marker.style.left = `${(index / denominator) * 100}%`;
    marker.title = String(stage?.label || `Stage ${index + 1}`);
    progressMilestonesEl.appendChild(marker);
  });
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

function renderStageList(stages) {
  if (!stageListEl) {
    return;
  }

  const safeStages = Array.isArray(stages) && stages.length ? stages : buildDefaultStages();
  stageListEl.innerHTML = "";

  safeStages.forEach((stage, index) => {
    const row = document.createElement("li");
    const status = String(stage.status || "pending").trim().toLowerCase() || "pending";
    row.className = `iac-stage-item is-${status}`;

    const badge = document.createElement("span");
    badge.className = "iac-stage-item__badge";
    if (status === "completed") {
      badge.textContent = "✓";
    } else if (status === "running") {
      badge.textContent = "…";
    } else if (status === "error") {
      badge.textContent = "!";
    } else {
      badge.textContent = String(index + 1);
    }

    const body = document.createElement("div");
    body.className = "iac-stage-item__body";

    const title = document.createElement("div");
    title.className = "iac-stage-item__title";
    title.textContent = String(stage.label || `Stage ${index + 1}`);

    const message = document.createElement("div");
    message.className = "iac-stage-item__message";
    if (stage.message) {
      message.textContent = String(stage.message);
    } else if (status === "completed") {
      message.textContent = "Completed";
    } else if (status === "running") {
      message.textContent = "In progress";
    } else if (status === "error") {
      message.textContent = "Failed";
    } else {
      message.textContent = "Waiting";
    }

    body.appendChild(title);
    body.appendChild(message);
    row.appendChild(badge);
    row.appendChild(body);
    stageListEl.appendChild(row);
  });

  renderProgressMilestones(safeStages);
}

function buildFileTree(files) {
  const root = {
    type: "dir",
    name: "",
    path: "",
    children: new Map()
  };

  files.forEach((file) => {
    const safePath = String(file.path || "").trim();
    if (!safePath) {
      return;
    }

    const parts = safePath.split("/").filter(Boolean);
    let cursor = root;
    let cursorPath = "";

    parts.forEach((part, index) => {
      const isLeaf = index === parts.length - 1;
      cursorPath = cursorPath ? `${cursorPath}/${part}` : part;

      if (isLeaf) {
        cursor.children.set(part, {
          type: "file",
          name: part,
          path: safePath,
          file
        });
        return;
      }

      if (!cursor.children.has(part)) {
        cursor.children.set(part, {
          type: "dir",
          name: part,
          path: cursorPath,
          children: new Map()
        });
      }

      const child = cursor.children.get(part);
      if (child && child.type === "dir") {
        cursor = child;
      }
    });
  });

  return root;
}

function renderTreeChildren(container, node, depth = 0) {
  const entries = Array.from(node.children.values()).sort((left, right) => {
    if (left.type !== right.type) {
      return left.type === "dir" ? -1 : 1;
    }
    return String(left.name || "").localeCompare(String(right.name || ""));
  });

  entries.forEach((entry) => {
    if (entry.type === "dir") {
      const dirRow = document.createElement("button");
      dirRow.type = "button";
      dirRow.className = "iac-tree-item is-folder";
      dirRow.style.paddingLeft = `${12 + depth * 16}px`;
      const isCollapsed = state.collapsedDirs.has(entry.path);

      const chevron = document.createElement("span");
      chevron.className = "iac-tree-item__chevron";
      chevron.textContent = isCollapsed ? "▸" : "▾";

      const dirLabel = document.createElement("span");
      dirLabel.className = "iac-tree-item__label";
      dirLabel.textContent = `${entry.name}/`;
      dirRow.appendChild(chevron);
      dirRow.appendChild(dirLabel);

      dirRow.addEventListener("click", () => {
        if (state.collapsedDirs.has(entry.path)) {
          state.collapsedDirs.delete(entry.path);
        } else {
          state.collapsedDirs.add(entry.path);
        }
        renderFileList();
      });

      container.appendChild(dirRow);

      if (!isCollapsed) {
        renderTreeChildren(container, entry, depth + 1);
      }
      return;
    }

    const fileRow = document.createElement("button");
    fileRow.type = "button";
    fileRow.className = "iac-tree-item is-file";
    fileRow.dataset.path = entry.path;
    fileRow.style.paddingLeft = `${12 + depth * 16}px`;
    if (entry.path === state.selectedPath) {
      fileRow.classList.add("is-active");
    }

    const label = document.createElement("span");
    label.className = "iac-tree-item__label";
    label.textContent = entry.name;
    const fileIndent = document.createElement("span");
    fileIndent.className = "iac-tree-item__chevron";
    fileIndent.textContent = "";
    fileRow.appendChild(fileIndent);
    fileRow.appendChild(label);

    fileRow.addEventListener("click", () => {
      selectFile(entry.path);
    });

    container.appendChild(fileRow);
  });
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

  const treeRoot = buildFileTree(state.files);
  renderTreeChildren(fileListEl, treeRoot, 0);
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
    cloud: String(project.cloud || ""),
    iacParameterFormat: String(project.iacParameterFormat || "bicepparam").trim().toLowerCase()
  };

  if (!state.parameterFormat) {
    state.parameterFormat = state.project.iacParameterFormat === "json" ? "json" : "bicepparam";
  }

  if (contextEl) {
    const projectName = String(state.project.name || "").trim() || "Project";
    contextEl.textContent = `(${projectName})`;
  }
}

async function loadFiles({ updateErrorStatus = true } = {}) {
  if (!state.projectId) {
    return false;
  }

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
      .filter((file) => file.path)
      .sort((left, right) => left.path.localeCompare(right.path));

    if (fileCountEl) {
      const label = state.files.length === 1 ? "file" : "files";
      fileCountEl.textContent = `${state.files.length} ${label}`;
    }

    if (!state.files.length) {
      state.selectedPath = "";
      renderFileList();
      updateViewerTitle("");
      setFileContent("No IaC files available yet. Generation output will appear here.", true);
      return true;
    }

    const hasSelection = state.files.some((file) => file.path === state.selectedPath);
    if (!hasSelection) {
      state.selectedPath = state.files[0].path;
    }

    renderFileList();
    await loadFileContent(state.selectedPath);
    return true;
  } catch (error) {
    state.files = [];
    state.selectedPath = "";
    renderFileList();
    updateViewerTitle("");
    setFileContent(error?.message || "Failed to load IaC files.", true);
    if (updateErrorStatus) {
      setStatus("error", {
        text: "IaC status: error",
        meta: error?.message || "Unable to load IaC files.",
        progress: 0
      });
    }
    return false;
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
    setFileContent(error?.message || "Unable to load file contents.", true);
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

function renderTask(task) {
  const normalized = normalizeTask(task);
  if (!normalized) {
    renderStageList(buildDefaultStages());
    return;
  }

  state.latestTask = normalized;
  state.taskId = normalized.taskId;
  syncTaskUrl(normalized.taskId);
  renderStageList(normalized.stages);

  if (normalized.status === "completed") {
    setStatus("complete", {
      text: "IaC status: generation complete",
      meta: normalized.message || "All stages completed successfully.",
      progress: 100
    });
    return;
  }

  if (normalized.status === "error") {
    setStatus("error", {
      text: "IaC status: error",
      meta: normalized.error || normalized.message || "IaC generation failed.",
      progress: normalized.progress
    });
    return;
  }

  if (normalized.status === "queued") {
    setStatus("running", {
      text: "IaC status: queued",
      meta: normalized.message || "Generation task is queued.",
      progress: normalized.progress || 1
    });
    return;
  }

  setStatus("running", {
    text: "IaC status: generating code",
    meta: normalized.message || "Generation in progress.",
    progress: normalized.progress
  });
}

function stopTaskPolling() {
  if (state.pollTimer) {
    window.clearTimeout(state.pollTimer);
    state.pollTimer = null;
  }
}

function scheduleTaskPolling(delayMs = 1200) {
  stopTaskPolling();
  state.pollTimer = window.setTimeout(() => {
    pollTaskStatus();
  }, delayMs);
}

async function fetchTask(taskId) {
  if (!state.projectId || !taskId) {
    return null;
  }

  const response = await fetch(
    `/api/project/${encodeURIComponent(state.projectId)}/iac/task/${encodeURIComponent(taskId)}`,
    { cache: "no-store" }
  );

  if (!response.ok) {
    throw new Error("Unable to fetch generation status.");
  }

  const payload = await response.json();
  return normalizeTask(payload?.task || null);
}

async function fetchLatestTask() {
  if (!state.projectId) {
    return null;
  }

  const response = await fetch(
    `/api/project/${encodeURIComponent(state.projectId)}/iac/task/latest`,
    { cache: "no-store" }
  );

  if (response.status === 404) {
    return null;
  }

  if (!response.ok) {
    throw new Error("Unable to load latest IaC task.");
  }

  const payload = await response.json();
  return normalizeTask(payload?.task || null);
}

async function pollTaskStatus() {
  if (!state.projectId || !state.taskId) {
    return;
  }

  try {
    const task = await fetchTask(state.taskId);
    if (!task) {
      throw new Error("Generation task was not found.");
    }

    renderTask(task);

    if (task.status === "queued" || task.status === "running") {
      scheduleTaskPolling(1200);
      return;
    }

    stopTaskPolling();

    if (task.status === "completed" && state.loadedTaskResultId !== task.taskId) {
      state.loadedTaskResultId = task.taskId;
      await loadFiles({ updateErrorStatus: true });
    }
  } catch (error) {
    setStatus("error", {
      text: "IaC status: error",
      meta: error?.message || "Unable to poll generation status.",
      progress: 0
    });
    scheduleTaskPolling(1800);
  }
}

async function startGenerationTask() {
  if (!state.projectId) {
    return;
  }

  state.loadedTaskResultId = "";
  renderStageList(buildDefaultStages());
  setStatus("running", {
    text: "IaC status: starting generation",
    meta: "Creating generation task...",
    progress: 1
  });

  try {
    const parameterFormat = state.parameterFormat === "json" ? "json" : "bicepparam";
    const response = await fetch(`/api/project/${encodeURIComponent(state.projectId)}/iac/task/start`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify({
        parameterFormat
      })
    });

    if (!response.ok) {
      let message = "Unable to start IaC generation.";
      try {
        const errorPayload = await response.json();
        if (errorPayload?.detail) {
          message = String(errorPayload.detail);
        }
      } catch {
      }
      throw new Error(message);
    }

    const payload = await response.json();
    const task = normalizeTask(payload?.task || null);
    if (!task) {
      throw new Error("Generation task payload is invalid.");
    }

    renderTask(task);

    if (task.status === "queued" || task.status === "running") {
      scheduleTaskPolling(700);
      return;
    }

    if (task.status === "completed") {
      state.loadedTaskResultId = task.taskId;
      await loadFiles({ updateErrorStatus: true });
    }
  } catch (error) {
    const failedStages = buildDefaultStages();
    failedStages[0].status = "error";
    failedStages[0].message = error?.message || "Failed to start generation.";
    renderStageList(failedStages);
    setStatus("error", {
      text: "IaC status: error",
      meta: error?.message || "Unable to start generation.",
      progress: 0
    });
  }
}

function handleBack() {
  stopTaskPolling();
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
    if (statusMetaEl) {
      const previous = statusMetaEl.textContent;
      statusMetaEl.textContent = "Copied file content to clipboard.";
      window.setTimeout(() => {
        if (statusMetaEl && statusMetaEl.textContent === "Copied file content to clipboard.") {
          statusMetaEl.textContent = previous;
        }
      }, 1600);
    }
  } catch {
    if (statusMetaEl) {
      statusMetaEl.textContent = "Copy failed.";
    }
  }
}

async function initialize() {
  const params = getParams();
  state.projectId = params.projectId;
  state.taskId = params.taskId;
  state.autostart = params.autostart;
  state.parameterFormat = params.parameterFormat === "json" ? "json" : "";

  const intent = consumeAutostartIntent(state.projectId);
  if (!state.autostart && intent.autostart) {
    state.autostart = true;
  }
  if (!state.parameterFormat && intent.parameterFormat) {
    state.parameterFormat = intent.parameterFormat;
  }

  const fromCanvasReferrer = /\/canvas\.html(?:\?|$)/.test(String(document.referrer || ""));
  if (!state.autostart && fromCanvasReferrer) {
    state.autostart = true;
  }

  btnBack?.addEventListener("click", handleBack);
  btnCopy?.addEventListener("click", () => {
    handleCopy();
  });
  window.addEventListener("beforeunload", stopTaskPolling);

  renderStageList(buildDefaultStages());
  setStatus("idle", {
    text: "IaC status: waiting",
    meta: "Task not started.",
    progress: 0
  });

  if (!state.projectId) {
    setStatus("error", {
      text: "IaC status: missing project context",
      meta: "Project ID is required.",
      progress: 0
    });
    setFileContent("Project ID is missing. Return to the canvas and try again.", true);
    renderFileList();
    return;
  }

  try {
    await loadProject(state.projectId);
  } catch (error) {
    setStatus("error", {
      text: "IaC status: error",
      meta: error?.message || "Unable to load project details.",
      progress: 0
    });
    setFileContent("Project details could not be loaded.", true);
    renderFileList();
    return;
  }

  await loadFiles({ updateErrorStatus: false });

  if (state.autostart) {
    syncTaskUrl(state.taskId);
    await startGenerationTask();
    return;
  }

  try {
    const task = state.taskId ? await fetchTask(state.taskId) : await fetchLatestTask();
    if (task) {
      renderTask(task);
      if (task.status === "queued" || task.status === "running") {
        scheduleTaskPolling(900);
      } else if (task.status === "completed") {
        state.loadedTaskResultId = task.taskId;
        await loadFiles({ updateErrorStatus: false });
      }
      return;
    }
  } catch {
  }

  if (state.files.length) {
    setStatus("idle", {
      text: "IaC status: files ready",
      meta: "No active generation task. Existing IaC files are shown below.",
      progress: 0
    });
  } else {
    setStatus("idle", {
      text: "IaC status: waiting",
      meta: "Use Generate Code from canvas to start IaC generation.",
      progress: 0
    });
  }
}

initialize().catch(() => {
  setStatus("error", {
    text: "IaC status: page failed to load",
    meta: "Initialization failed.",
    progress: 0
  });
  setFileContent("Initialization failed.", true);
});
