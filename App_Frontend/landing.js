// ===== UI Element References =====
const introCloudSelect = document.getElementById("intro-cloud");
const introNameInput = document.getElementById("intro-name");
const btnIntroCreate = document.getElementById("btn-intro-create");
const cloudHeaders = Array.from(document.querySelectorAll(".cloud-header"));

// ===== State =====
const state = {
  projects: []
};

// ===== Utility Functions =====
function generateProjectName(cloud) {
  const now = new Date();
  const date = now.toISOString().slice(0, 10).replace(/-/g, "-");
  const time = now.toTimeString().slice(0, 8).replace(/:/g, "-");
  return `${cloud.toLowerCase()}-${date}-${time}`;
}

function formatTimestamp(ms) {
  const date = new Date(ms);
  return date.toLocaleString();
}

function titleCase(value) {
  return value
    .split(" ")
    .map((part) => part.charAt(0).toUpperCase() + part.slice(1))
    .join(" ");
}

function getCloudIconRoot(cloudName) {
  if (cloudName === "Azure") {
    return "/icons/azure";
  }
  return "";
}

// ===== LocalStorage =====
function saveProjects() {
  localStorage.setItem("a3_projects", JSON.stringify(state.projects));
}

function loadProjects() {
  const stored = localStorage.getItem("a3_projects");
  state.projects = stored ? JSON.parse(stored) : [];
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
    openBtn.textContent = "↗";
    openBtn.title = "Open";
    openBtn.addEventListener("click", (e) => {
      e.stopPropagation();
      openProject(project.id);
    });

    const deleteBtn = document.createElement("button");
    deleteBtn.className = "delete";
    deleteBtn.textContent = "✕";
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

function createProject() {
  const cloud = introCloudSelect.value;
  const name = introNameInput.value.trim() || generateProjectName(cloud);

  if (!cloud) {
    alert("Please select a cloud provider");
    return;
  }

  const project = {
    id: `${cloud.toLowerCase()}-${Date.now()}`,
    name,
    cloud,
    lastSaved: Date.now()
  };

  state.projects.push(project);
  saveProjects();
  introNameInput.value = "";
  introCloudSelect.value = "";
  renderProjectsList();
  openProject(project.id);
}

function deleteProject(projectId) {
  if (!confirm("Delete this project? This cannot be undone.")) {
    return;
  }

  state.projects = state.projects.filter((p) => p.id !== projectId);
  saveProjects();
  renderProjectsList();
}

// ===== Accordion Behavior =====
function toggleCloudSection(cloud) {
  const header = document.querySelector(`.cloud-header[data-cloud="${cloud}"]`);
  const isExpanded = header.classList.contains("is-expanded");
  
  if (isExpanded) {
    // Close if already expanded
    header.classList.remove("is-expanded");
  } else {
    // Close all other sections
    cloudHeaders.forEach((otherHeader) => {
      if (otherHeader !== header) {
        otherHeader.classList.remove("is-expanded");
      }
    });
    // Open this section
    header.classList.add("is-expanded");
  }
}

// ===== Event Listeners =====
btnIntroCreate.addEventListener("click", createProject);

cloudHeaders.forEach((header) => {
  header.addEventListener("click", (e) => {
    e.preventDefault();
    const cloud = header.dataset.cloud;
    toggleCloudSection(cloud);
  });
});

// Auto-generate name when cloud changes
introCloudSelect.addEventListener("change", () => {
  if (!introNameInput.value || introNameInput.value.split("-")[0] === "azure" || introNameInput.value.split("-")[0] === "aws" || introNameInput.value.split("-")[0] === "gcp") {
    introNameInput.value = generateProjectName(introCloudSelect.value);
  }
});

// ===== Initialization =====
loadProjects();
renderProjectsList();
