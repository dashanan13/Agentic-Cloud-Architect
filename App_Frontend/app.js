const app = document.getElementById("app");
const cloudProviderSelect = document.getElementById("cloud-provider");
const resourceListEl = document.getElementById("resource-list");
const searchInput = document.querySelector('input[type="search"]');
const selectedResourceNameEl = document.getElementById("selected-resource-name");
const propertyContentEl = document.getElementById("property-content");
const tabs = Array.from(document.querySelectorAll(".tab"));
const panels = {
  chat: document.getElementById("panel-chat"),
  terminal: document.getElementById("panel-terminal")
};

const cloudCatalogs = {};

const state = {
  leftWidth: 280,
  rightWidth: 320,
  bottomHeight: 220,
  bottomRightWidth: 320,
  selectedCloud: "Azure",
  selectedResource: null,
  searchTerm: ""
};

const constraints = {
  leftMin: 220,
  leftMax: 480,
  rightMin: 260,
  rightMax: 500,
  bottomMin: 160,
  bottomMax: 380,
  bottomRightMin: 240,
  bottomRightMax: 520
};

function applySizes() {
  app.style.setProperty("--left-width", `${state.leftWidth}px`);
  app.style.setProperty("--right-width", `${state.rightWidth}px`);
  app.style.setProperty("--bottom-height", `${state.bottomHeight}px`);
  app.style.setProperty("--bottom-right-width", `${state.bottomRightWidth}px`);
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
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

async function loadCatalogForCloud(cloudName) {
  if (Object.hasOwn(cloudCatalogs, cloudName)) {
    return;
  }

  try {
    const response = await fetch(`./catalogs/${cloudName.toLowerCase()}.json`, { cache: "no-store" });
    if (!response.ok) {
      throw new Error(`Failed to load ${cloudName} catalog: ${response.status}`);
    }

    cloudCatalogs[cloudName] = await response.json();
  } catch {
    cloudCatalogs[cloudName] = null;
  }
}

function updatePropertyPanel(resourceName) {
  if (!resourceName) {
    selectedResourceNameEl.textContent = "None selected";
    propertyContentEl.textContent = "Select a resource from the left panel to preview properties.";
    return;
  }

  selectedResourceNameEl.textContent = resourceName;
  propertyContentEl.innerHTML = [
    "Properties",
    "- Name",
    "- Region",
    "- SKU / Tier",
    "- Tags"
  ].join("<br />");
}

function createResourceRow(category, resource, iconRoot) {
  const row = document.createElement("button");
  row.className = "resource-row";
  row.type = "button";

  const icon = document.createElement("img");
  icon.className = "resource-icon";
  icon.alt = `${resource.name} icon`;
  icon.src = encodeURI(`${iconRoot}/${category}/${resource.icon}`);
  icon.loading = "lazy";

  const name = document.createElement("span");
  name.className = "resource-name";
  name.textContent = resource.name;

  row.appendChild(icon);
  row.appendChild(name);
  row.addEventListener("click", () => {
    state.selectedResource = resource.name;
    updatePropertyPanel(resource.name);
  });

  return row;
}

function renderResources() {
  resourceListEl.innerHTML = "";
  const cloudCatalog = cloudCatalogs[state.selectedCloud];

  if (!cloudCatalog) {
    const empty = document.createElement("div");
    empty.className = "resource-empty";
    empty.textContent = `${state.selectedCloud} resource catalog is not configured yet.`;
    resourceListEl.appendChild(empty);
    state.selectedResource = null;
    updatePropertyPanel(null);
    return;
  }

  const categories = Object.keys(cloudCatalog).sort((first, second) => first.localeCompare(second));
  const iconRoot = getCloudIconRoot(state.selectedCloud);
  const search = state.searchTerm.trim().toLowerCase();
  let hasVisibleRows = false;

  categories.forEach((category) => {
    const resources = [...cloudCatalog[category]].sort((first, second) => first.name.localeCompare(second.name));
    const filtered = search
      ? resources.filter((resource) => resource.name.toLowerCase().includes(search))
      : resources;

    if (!filtered.length) {
      return;
    }

    hasVisibleRows = true;
    const group = document.createElement("section");
    group.className = "resource-group";

    const heading = document.createElement("h4");
    heading.className = "resource-group-title";
    heading.textContent = titleCase(category);

    const groupBody = document.createElement("div");
    groupBody.className = "resource-group-body";

    filtered.forEach((resource) => {
      groupBody.appendChild(createResourceRow(category, resource, iconRoot));
    });

    group.appendChild(heading);
    group.appendChild(groupBody);
    resourceListEl.appendChild(group);
  });

  if (!hasVisibleRows) {
    const empty = document.createElement("div");
    empty.className = "resource-empty";
    empty.textContent = "No resources match your search.";
    resourceListEl.appendChild(empty);
  }
}

function startDrag(getValue) {
  function onMove(event) {
    getValue(event);
    applySizes();
  }

  function onUp() {
    window.removeEventListener("mousemove", onMove);
    window.removeEventListener("mouseup", onUp);
  }

  window.addEventListener("mousemove", onMove);
  window.addEventListener("mouseup", onUp);
}

document.querySelector('[data-splitter="left"]').addEventListener("mousedown", () => {
  startDrag((event) => {
    state.leftWidth = clamp(event.clientX, constraints.leftMin, constraints.leftMax);
  });
});

document.querySelector('[data-splitter="right"]').addEventListener("mousedown", () => {
  startDrag((event) => {
    const width = window.innerWidth - event.clientX;
    state.rightWidth = clamp(width, constraints.rightMin, constraints.rightMax);
  });
});

document.querySelector('[data-splitter="bottom"]').addEventListener("mousedown", () => {
  startDrag((event) => {
    const topBarHeight = 56;
    const height = window.innerHeight - topBarHeight - event.clientY;
    state.bottomHeight = clamp(height, constraints.bottomMin, constraints.bottomMax);
  });
});

document.querySelector('[data-splitter="bottom-right"]').addEventListener("mousedown", (mouseDownEvent) => {
  const bottomPanel = mouseDownEvent.currentTarget.parentElement.getBoundingClientRect();

  startDrag((event) => {
    const width = bottomPanel.right - event.clientX;
    state.bottomRightWidth = clamp(width, constraints.bottomRightMin, constraints.bottomRightMax);
  });
});

tabs.forEach((tab) => {
  function activateTab() {
    const name = tab.dataset.tab;

    tabs.forEach((item) => {
      const active = item === tab;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
      item.setAttribute("tabindex", active ? "0" : "-1");
    });

    Object.entries(panels).forEach(([panelName, panelEl]) => {
      const hidden = panelName !== name;
      panelEl.classList.toggle("is-hidden", hidden);
      panelEl.toggleAttribute("hidden", hidden);
    });
  }

  tab.addEventListener("click", activateTab);
  tab.addEventListener("focus", activateTab);
  tab.addEventListener("keydown", (event) => {
    if (event.key === "Enter" || event.key === " ") {
      event.preventDefault();
      activateTab();
    }

    if (event.key === "ArrowRight" || event.key === "ArrowLeft") {
      event.preventDefault();
      const currentIndex = tabs.indexOf(tab);
      const direction = event.key === "ArrowRight" ? 1 : -1;
      const nextIndex = (currentIndex + direction + tabs.length) % tabs.length;
      tabs[nextIndex].focus();
    }
  });
});

cloudProviderSelect.addEventListener("change", () => {
  state.selectedCloud = cloudProviderSelect.value;
  state.selectedResource = null;
  loadCatalogForCloud(state.selectedCloud).then(() => {
    renderResources();
    updatePropertyPanel(null);
  });
});

searchInput.addEventListener("input", () => {
  state.searchTerm = searchInput.value;
  renderResources();
});

applySizes();
updatePropertyPanel(null);

loadCatalogForCloud(state.selectedCloud).then(() => {
  renderResources();
});
