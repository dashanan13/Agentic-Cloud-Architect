// ===== UI Element References =====
const btnBackProjects = document.getElementById("btn-back-projects");
const btnProjectSave = document.getElementById("btn-project-save");
const btnProjectSettings = document.getElementById("btn-project-settings");
const btnValidate = document.getElementById("btn-validate");
const btnExportDiagram = document.getElementById("btn-export-diagram");
const btnGenerateCode = document.getElementById("btn-generate-code");
const projectSaveStatus = document.getElementById("project-save-status");
const projectNameDisplay = document.getElementById("project-name-suffix-display");
const projectNamePrefixDisplay = document.getElementById("project-name-prefix-display");
const resourceListEl = document.getElementById("resource-list");
const searchInput = document.getElementById("search-input");
const selectedResourceNameEl = document.getElementById("selected-resource-name");
const propertyContentEl = document.getElementById("property-content");
const appEl = document.getElementById("app");
const canvasViewportEl = document.getElementById("canvas-viewport");
const canvasGridEl = document.getElementById("canvas-grid");
const canvasLayerEl = document.getElementById("canvas-layer");
const canvasZoomOutBtn = document.getElementById("canvas-zoom-out");
const canvasZoomInBtn = document.getElementById("canvas-zoom-in");
const canvasResetViewBtn = document.getElementById("canvas-reset-view");
const canvasZoomLabelEl = document.getElementById("canvas-zoom-label");
const canvasStatusEl = document.getElementById("canvas-status");
const canvasEdgesEl = document.getElementById("canvas-edges");
const workspaceEl = document.getElementById("workspace");
const statusLeftWidthEl = document.getElementById("status-left-width");
const statusRightWidthEl = document.getElementById("status-right-width");
const chatHistoryEl = document.getElementById("chat-history");
const chatInputEl = document.getElementById("chat-input");
const chatSendBtn = document.getElementById("chat-send");
const chatRuntimeModelEl = document.getElementById("chat-runtime-model");
const chatRuntimeMcpEl = document.getElementById("chat-runtime-mcp");
const chatRuntimeCtxEl = document.getElementById("chat-runtime-ctx");
const projectIacIconEl = document.getElementById("project-iac-icon");
const tipsContentEl = document.getElementById("tips-content");

function setSelectedResourceName(value) {
  if (selectedResourceNameEl) {
    selectedResourceNameEl.textContent = value;
  }
}

// IaC tool icon paths served from the static icons folder.
const IAC_ICONS = {
  bicep:     "/icons/azure-bicep-icon.png",
  terraform: "/icons/terraform-icon.png",
  opentofu:  "/icons/terraform-icon.png",
};

// Known context window sizes (tokens) for Azure AI Foundry models.
// Keys are lowercase substrings matched against the model deployment name.
const MODEL_CONTEXT_WINDOWS = [
  { match: "kimi-k2",           tokens: 131072 },
  { match: "o3-mini",           tokens: 200000 },
  { match: "o3",                tokens: 200000 },
  { match: "o1-mini",           tokens: 128000 },
  { match: "o1",                tokens: 200000 },
  { match: "gpt-4o-mini",       tokens: 128000 },
  { match: "gpt-4o",            tokens: 128000 },
  { match: "gpt-4-turbo",       tokens: 128000 },
  { match: "gpt-4",             tokens: 8192   },
  { match: "gpt-35-turbo",      tokens: 16385  },
  { match: "deepseek-r1",       tokens: 128000 },
  { match: "deepseek-v3",       tokens: 128000 },
  { match: "deepseek",          tokens: 128000 },
  { match: "phi-4-multimodal",  tokens: 16384  },
  { match: "phi-4",             tokens: 16384  },
  { match: "llama-3.3",         tokens: 128000 },
  { match: "llama-3.1",         tokens: 128000 },
  { match: "llama-3",           tokens: 8192   },
  { match: "mistral-large",     tokens: 131072 },
  { match: "mistral-small",     tokens: 32768  },
  { match: "mistral",           tokens: 32768  },
  { match: "cohere-command-r",  tokens: 131072 },
  { match: "jamba",             tokens: 256000 },
];

function formatContextWindow(modelName) {
  if (!modelName) return null;
  const lower = modelName.toLowerCase();
  for (const entry of MODEL_CONTEXT_WINDOWS) {
    if (lower.includes(entry.match)) {
      const k = entry.tokens / 1024;
      return k >= 1 ? `${Math.round(k)}K tokens` : `${entry.tokens} tokens`;
    }
  }
  return null;
}
const chatInitialMarkup = chatHistoryEl ? chatHistoryEl.innerHTML : "";
const tipsInitialMarkup = tipsContentEl ? tipsContentEl.innerHTML : "";
let chatAgentState = null;
let chatRequestInFlight = false;
let saveRequestInFlight = false;
let queuedSaveOptions = null;
let activeSavePromise = Promise.resolve();
let validationStatusState = null;
let validationResultState = null;
let validationExpandedSeverity = null;
let validationRunInFlight = false;
const validationFixInFlightFindingIds = new Set();
const validationFixStatusByFindingId = new Map();
const tabGroups = Array.from(document.querySelectorAll('[role="tablist"]'))
  .map((tabListEl) => {
    const groupTabs = Array.from(tabListEl.querySelectorAll('.tab[role="tab"]'));
    if (!groupTabs.length) {
      return null;
    }

    const groupPanels = new Map();
    groupTabs.forEach((tab) => {
      const panelId = tab.getAttribute("aria-controls");
      const panelEl = panelId ? document.getElementById(panelId) : null;
      if (panelEl && tab.dataset.tab) {
        groupPanels.set(tab.dataset.tab, panelEl);
      }
    });

    return {
      tabs: groupTabs,
      panels: groupPanels
    };
  })
  .filter(Boolean);

function readCssPxVar(variableName, fallback) {
  const rootStyle = getComputedStyle(document.documentElement);
  const rawValue = rootStyle.getPropertyValue(variableName).trim();
  const parsedValue = Number.parseFloat(rawValue);
  return Number.isFinite(parsedValue) ? parsedValue : fallback;
}

const layoutConfig = {
  leftDefault: readCssPxVar("--layout-left-default", 15),
  leftMin: readCssPxVar("--layout-left-min", 10),
  leftMax: readCssPxVar("--layout-left-max", 30),
  rightDefault: readCssPxVar("--layout-right-default", 20),
  rightMin: readCssPxVar("--layout-right-min", 15),
  rightMax: readCssPxVar("--layout-right-max", 40),
  bottomDefault: readCssPxVar("--layout-bottom-default", 130),
  bottomMin: readCssPxVar("--layout-bottom-min", 120),
  bottomMax: readCssPxVar("--layout-bottom-max", 380),
  bottomRightDefault: readCssPxVar("--layout-bottom-right-default", 220),
  bottomRightMin: readCssPxVar("--layout-bottom-right-min", 200),
  bottomRightMax: readCssPxVar("--layout-bottom-right-max", 520)
};

// ===== State =====
const cloudCatalogs = {};
const propertyPanelExpansionStateByKey = new Map();
let propertyPanelRenderedStateKey = "";
const state = {
  projects: [],
  currentProject: null,
  leftWidth: layoutConfig.leftDefault,
  rightWidth: layoutConfig.rightDefault,
  bottomHeight: layoutConfig.bottomDefault,
  bottomRightWidth: layoutConfig.bottomRightDefault,
  selectedResource: null,
  searchTerm: "",
  canvasView: {
    x: 0,
    y: 0,
    zoom: 1
  },
  canvasItems: [],
  canvasConnections: [],
  selectedConnectionId: null,
  edgeDraft: {
    active: false,
    sourceId: null,
    sourceAnchor: "right",
    targetId: null,
    targetAnchor: "left",
    endClientX: 0,
    endClientY: 0
  }
};

const canvasInteraction = {
  isPanning: false,
  panOriginX: 0,
  panOriginY: 0,
  viewOriginX: 0,
  viewOriginY: 0,
  draggingItemId: null,
  dragOriginX: 0,
  dragOriginY: 0,
  itemOriginX: 0,
  itemOriginY: 0,
  resizingItemId: null,
  resizeOriginX: 0,
  resizeOriginY: 0,
  widthOrigin: 0,
  heightOrigin: 0
};

const constraints = {
  leftMin: layoutConfig.leftMin,
  leftMax: layoutConfig.leftMax,
  rightMin: layoutConfig.rightMin,
  rightMax: layoutConfig.rightMax,
  bottomMin: layoutConfig.bottomMin,
  bottomMax: layoutConfig.bottomMax,
  bottomRightMin: layoutConfig.bottomRightMin,
  bottomRightMax: layoutConfig.bottomRightMax
};

const MAX_PROJECT_NAME_LENGTH = 50;
const AUTOSAVE_INTERVAL_MS = 30000;
const CANVAS_ZOOM = {
  min: 0.05,
  max: 3,
  step: 0.05,
  grid: 40
};

const CANVAS_CONTAINER = {
  minWidth: 220,
  minHeight: 140,
  defaultWidth: 360,
  defaultHeight: 240,
  headerHeight: 32,
  padding: 12
};

const RESOURCE_BOOLEAN_SELECT_FIELDS = new Set([
  "disableBgpRoutePropagation",
  "enableVirtualNetworkEncryption",
  "enableAzureBastion",
  "enableAzureFirewall",
  "enableDdosProtection",
  "subnetPrivate"
]);

const SUBNET_PURPOSE_OPTIONS = [
  "default",
  "bastion",
  "firewall",
  "firewall-management",
  "virtual-network-gateway",
  "route-server"
];

const SUBNET_PURPOSE_NAMES = {
  default: "default",
  bastion: "AzureBastionSubnet",
  firewall: "AzureFirewallSubnet",
  "firewall-management": "AzureFirewallManagementSubnet",
  "virtual-network-gateway": "GatewaySubnet",
  "route-server": "RouteServerSubnet"
};

const RESOURCE_ENUM_SELECT_OPTIONS = {
  firewallTier: ["basic", "standard", "premium"],
  subnetPurpose: SUBNET_PURPOSE_OPTIONS,
  privateEndpointNetworkPolicies: ["disabled", "enabled"],
  ipVersion: ["ipv4", "ipv6"],
  sku: ["standard", "standardv2"],
  zone: ["zone-redundant", "1", "2", "3"],
  tier: ["regional", "global"],
  routingPreference: ["microsoft-network", "internet"],
  dnsLabelScope: ["none", "no-reuse", "resource-group", "subscription", "tenant"],
  ddosProtection: ["network", "ip", "disabled"]
};

function normalizeLabel(value) {
  return String(value || "").trim().toLowerCase();
}

function escapeHtml(value) {
  return String(value ?? "")
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&#39;");
}

function sanitizeTagList(tags) {
  if (!Array.isArray(tags)) {
    return [];
  }

  return tags
    .map((tag) => ({
      key: String(tag?.key ?? tag?.label ?? "").trim().slice(0, 128),
      value: String(tag?.value ?? "").trim().slice(0, 256)
    }))
    .filter((tag) => Boolean(tag.key || tag.value));
}

function buildEditableTagRows(tags) {
  const normalizedTags = sanitizeTagList(tags);
  return [
    ...normalizedTags,
    { key: "", value: "" }
  ];
}

function sanitizeStringList(value, options = {}) {
  const {
    maxItems = 40,
    maxLength = 200,
    allowCsv = true,
    fallback = []
  } = options;

  const fallbackList = Array.isArray(fallback)
    ? fallback
    : (allowCsv && typeof fallback === "string" ? fallback.split(",") : (fallback == null ? [] : [fallback]));

  const source = Array.isArray(value)
    ? value
    : (allowCsv && typeof value === "string"
      ? value.split(",")
      : fallbackList);

  const seen = new Set();
  const cleaned = [];

  source.forEach((entry) => {
    const rawValue = entry && typeof entry === "object"
      ? (entry.name ?? entry.service ?? entry.id ?? "")
      : entry;
    const normalized = String(rawValue ?? "").trim();
    if (!normalized) {
      return;
    }

    const normalizedKey = normalized.toLowerCase();
    if (seen.has(normalizedKey)) {
      return;
    }

    seen.add(normalizedKey);
    cleaned.push(normalized.slice(0, maxLength));
  });

  return cleaned.slice(0, maxItems);
}

function normalizeSecurityRuleDirection(value) {
  return String(value || "").toLowerCase() === "outbound" ? "outbound" : "inbound";
}

function normalizeSecurityRuleAccess(value) {
  return String(value || "").toLowerCase() === "deny" ? "deny" : "allow";
}

function normalizeSecurityRuleProtocol(value) {
  const normalized = String(value || "").toLowerCase();
  if (normalized === "*" || normalized === "any") {
    return "any";
  }
  if (["tcp", "udp", "icmp"].includes(normalized)) {
    return normalized;
  }
  return "tcp";
}

function sanitizeNetworkSecurityRules(value) {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.slice(0, 100).map((rule, index) => {
    const fallbackPriority = clamp(100 + (index * 10), 100, 4096);
    const sanitizedName = String(rule?.name || `rule-${index + 1}`).trim().slice(0, 80);
    return {
      name: sanitizedName || `rule-${index + 1}`,
      priority: normalizeIntegerValue(rule?.priority, fallbackPriority, 100, 4096),
      direction: normalizeSecurityRuleDirection(rule?.direction),
      access: normalizeSecurityRuleAccess(rule?.access),
      protocol: normalizeSecurityRuleProtocol(rule?.protocol),
      sourceAddressPrefix: String(rule?.sourceAddressPrefix || "*").trim() || "*",
      sourcePortRange: String(rule?.sourcePortRange || "*").trim() || "*",
      destinationAddressPrefix: String(rule?.destinationAddressPrefix || "*").trim() || "*",
      destinationPortRange: String(rule?.destinationPortRange || "*").trim() || "*"
    };
  });
}

function normalizeRouteNextHopType(value) {
  const normalized = String(value || "").toLowerCase();
  const allowed = [
    "virtual-network-gateway",
    "vnet-local",
    "internet",
    "virtual-appliance",
    "none"
  ];
  return allowed.includes(normalized) ? normalized : "internet";
}

function sanitizeRouteDefinitions(value) {
  if (!Array.isArray(value)) {
    return [];
  }

  return value.slice(0, 100).map((route, index) => {
    const sanitizedName = String(route?.name || `route-${index + 1}`).trim().slice(0, 80);
    return {
      name: sanitizedName || `route-${index + 1}`,
      addressPrefix: String(route?.addressPrefix || "0.0.0.0/0").trim() || "0.0.0.0/0",
      nextHopType: normalizeRouteNextHopType(route?.nextHopType),
      nextHopIpAddress: String(route?.nextHopIpAddress || "").trim()
    };
  });
}

function buildEditableStringRows(values, options = {}) {
  const normalizedValues = sanitizeStringList(values, options);
  return [
    ...normalizedValues,
    ""
  ];
}

function extractDelegationServiceNames(value) {
  if (!Array.isArray(value)) {
    return [];
  }

  const rawServices = value.map((delegation) => {
    if (delegation && typeof delegation === "object") {
      return delegation.serviceName || delegation.name || "";
    }
    return delegation;
  });

  return sanitizeStringList(rawServices, {
    maxItems: 40,
    maxLength: 120,
    allowCsv: true
  });
}

function buildDelegationsFromServiceNames(value) {
  const serviceNames = extractDelegationServiceNames(Array.isArray(value) ? value : [value]);
  return serviceNames.map((serviceName, index) => ({
    name: `delegation-${index + 1}`,
    serviceName
  }));
}

function sanitizeSubnetDelegations(value) {
  return buildDelegationsFromServiceNames(value);
}

function sanitizeDnsNameValue(value) {
  return String(value ?? "").trim().replace(/^\.+|\.+$/g, "");
}

function isGeneratedResourceName(value) {
  return /^resource\s+\d+$/i.test(String(value || "").trim());
}

function splitDnsName(value) {
  const cleaned = sanitizeDnsNameValue(value);
  const segments = cleaned.split(".").map((segment) => segment.trim()).filter(Boolean);

  if (segments.length < 2) {
    return {
      parentZoneName: "",
      childDomainName: cleaned
    };
  }

  return {
    parentZoneName: segments.slice(1).join("."),
    childDomainName: segments[0]
  };
}

function normalizeSubnetPurpose(value) {
  const normalized = String(value || "").toLowerCase();
  return SUBNET_PURPOSE_OPTIONS.includes(normalized) ? normalized : "default";
}

function getSubnetPredefinedName(purpose) {
  const normalizedPurpose = normalizeSubnetPurpose(purpose);
  return SUBNET_PURPOSE_NAMES[normalizedPurpose] || SUBNET_PURPOSE_NAMES.default;
}

function isSubnetNameLocked(purpose) {
  return normalizeSubnetPurpose(purpose) !== "default";
}

function resolveSubnetNameForPurpose(purpose, nameValue = "") {
  const normalizedPurpose = normalizeSubnetPurpose(purpose);
  if (isSubnetNameLocked(normalizedPurpose)) {
    return getSubnetPredefinedName(normalizedPurpose);
  }

  const customName = String(nameValue || "").trim();
  return customName || getSubnetPredefinedName("default");
}

function syncSubnetItemName(item) {
  if (!item || !isSubnetItem(item)) {
    return false;
  }

  const properties = ensureItemProperties(item);
  const subnetPurpose = normalizeSubnetPurpose(properties.subnetPurpose);
  const subnetName = isSubnetNameLocked(subnetPurpose)
    ? getSubnetPredefinedName(subnetPurpose)
    : resolveSubnetNameForPurpose(subnetPurpose, properties.subnetName);

  properties.subnetPurpose = subnetPurpose;
  properties.subnetName = subnetName;

  if (String(item.name || "") === subnetName) {
    return false;
  }

  item.name = subnetName;
  return true;
}

function buildDnsZoneEffectiveName(properties, options = {}) {
  const dnsMode = options.dnsMode === "child" || properties?.dnsMode === "child" ? "child" : "root";
  const dnsName = sanitizeDnsNameValue(options.dnsName ?? properties?.dnsName);
  const parentZoneName = sanitizeDnsNameValue(options.parentZoneName ?? properties?.parentZoneName);
  const childDomainName = sanitizeDnsNameValue(options.childDomainName ?? properties?.childDomainName);

  if (dnsMode === "child") {
    if (childDomainName && parentZoneName) {
      return `${childDomainName}.${parentZoneName}`;
    }
    return childDomainName || parentZoneName || "";
  }

  return dnsName;
}

function normalizeBooleanValue(value, fallback = false) {
  if (typeof value === "boolean") {
    return value;
  }

  if (typeof value === "number") {
    return value !== 0;
  }

  if (typeof value === "string") {
    const normalized = value.trim().toLowerCase();
    if (["true", "yes", "enabled", "on", "1"].includes(normalized)) {
      return true;
    }
    if (["false", "no", "disabled", "off", "0"].includes(normalized)) {
      return false;
    }
  }

  return fallback;
}

function normalizeIntegerValue(value, fallback, min, max) {
  const parsed = Number.parseInt(String(value ?? ""), 10);
  if (!Number.isInteger(parsed)) {
    return fallback;
  }
  return clamp(parsed, min, max);
}

function assignExistingDependency(properties, modeField, refField, nameField, resourceItem) {
  if (!properties || !resourceItem || !modeField || !refField || !nameField) {
    return;
  }

  properties[modeField] = "existing";
  properties[refField] = resourceItem.id;
  properties[nameField] = String(resourceItem.name || properties[nameField] || "");
}

function switchDependencyToCustom(properties, modeField, refField, nameField, fallbackName = "") {
  if (!properties || !modeField || !refField || !nameField) {
    return;
  }

  const currentItem = getItemById(properties[refField]);
  properties[modeField] = "custom";
  properties[refField] = "";
  properties[nameField] = String(fallbackName || currentItem?.name || properties[nameField] || "");
}

function normalizeResourceIdentity(value) {
  return normalizeLabel(value).replace(/[^a-z0-9]+/g, "");
}

function matchesResourceIdentity(value, aliases) {
  const normalizedValue = normalizeResourceIdentity(value);
  if (!normalizedValue || !Array.isArray(aliases)) {
    return false;
  }

  return aliases.some((alias) => {
    const normalizedAlias = normalizeResourceIdentity(alias);
    return normalizedAlias ? normalizedValue.includes(normalizedAlias) : false;
  });
}

function isResourceGroupTypeName(value) {
  return matchesResourceIdentity(value, [
    "resource group",
    "resource groups",
    "microsoft.resources/resourcegroups"
  ]);
}

function isDnsZoneTypeName(value) {
  return matchesResourceIdentity(value, [
    "dns zone",
    "dns zones",
    "microsoft.network/dnszones"
  ]);
}

function isNetworkSecurityGroupTypeName(value) {
  return matchesResourceIdentity(value, [
    "network security group",
    "network security groups",
    "nsg",
    "microsoft.network/networksecuritygroups"
  ]);
}

function isRouteTableTypeName(value) {
  return matchesResourceIdentity(value, [
    "route table",
    "route tables",
    "microsoft.network/routetables"
  ]);
}

function isVirtualNetworkTypeName(value) {
  return matchesResourceIdentity(value, [
    "virtual network",
    "virtual networks",
    "vnet",
    "microsoft.network/virtualnetworks"
  ]);
}

function isSubnetTypeName(value) {
  return matchesResourceIdentity(value, [
    "subnet",
    "subnets",
    "microsoft.network/virtualnetworks/subnets"
  ]);
}

function isPublicIpTypeName(value) {
  return matchesResourceIdentity(value, [
    "public ip address",
    "public ip addresses",
    "public ip",
    "microsoft.network/publicipaddresses"
  ]);
}

function isResourceGroupItem(item) {
  return isResourceGroupTypeName(item?.resourceType)
    || isResourceGroupTypeName(item?.resourceName)
    || isResourceGroupTypeName(item?.name);
}

function isDnsZoneItem(item) {
  return isDnsZoneTypeName(item?.resourceType)
    || isDnsZoneTypeName(item?.resourceName)
    || isDnsZoneTypeName(item?.name);
}

function isNetworkSecurityGroupItem(item) {
  return isNetworkSecurityGroupTypeName(item?.resourceType)
    || isNetworkSecurityGroupTypeName(item?.resourceName)
    || isNetworkSecurityGroupTypeName(item?.name);
}

function isRouteTableItem(item) {
  return isRouteTableTypeName(item?.resourceType)
    || isRouteTableTypeName(item?.resourceName)
    || isRouteTableTypeName(item?.name);
}

function isVirtualNetworkItem(item) {
  return isVirtualNetworkTypeName(item?.resourceType)
    || isVirtualNetworkTypeName(item?.resourceName)
    || isVirtualNetworkTypeName(item?.name);
}

function isSubnetItem(item) {
  return isSubnetTypeName(item?.resourceType)
    || isSubnetTypeName(item?.resourceName)
    || isSubnetTypeName(item?.name);
}

function isPublicIpItem(item) {
  return isPublicIpTypeName(item?.resourceType)
    || isPublicIpTypeName(item?.resourceName)
    || isPublicIpTypeName(item?.name);
}

function supportsResourceGroupBinding(item) {
  return isDnsZoneItem(item)
    || isNetworkSecurityGroupItem(item)
    || isRouteTableItem(item)
    || isVirtualNetworkItem(item)
    || isPublicIpItem(item);
}

function getCanvasResourceGroups(options = {}) {
  const { excludeId = null } = options;
  return state.canvasItems.filter((item) => item.id !== excludeId && isResourceGroupItem(item));
}

function getCanvasDnsZones(options = {}) {
  const { excludeId = null } = options;
  return state.canvasItems.filter((item) => item.id !== excludeId && isDnsZoneItem(item));
}

function getCanvasNetworkSecurityGroups(options = {}) {
  const { excludeId = null } = options;
  return state.canvasItems.filter((item) => item.id !== excludeId && isNetworkSecurityGroupItem(item));
}

function getCanvasRouteTables(options = {}) {
  const { excludeId = null } = options;
  return state.canvasItems.filter((item) => item.id !== excludeId && isRouteTableItem(item));
}

function getCanvasSubnets(options = {}) {
  const { excludeId = null } = options;
  return state.canvasItems.filter((item) => item.id !== excludeId && isSubnetItem(item));
}

function getCanvasVirtualNetworks(options = {}) {
  const { excludeId = null } = options;
  return state.canvasItems.filter((item) => item.id !== excludeId && isVirtualNetworkItem(item));
}

function getCanvasPublicIps(options = {}) {
  const { excludeId = null } = options;
  return state.canvasItems.filter((item) => item.id !== excludeId && isPublicIpItem(item));
}

function getConnectedCanvasItems(item, options = {}) {
  const { excludeId = null } = options;
  if (!item || !item.id) {
    return [];
  }

  const connectedItems = [];
  const seen = new Set();

  state.canvasConnections.forEach((connection) => {
    const fromId = String(connection.fromId || "");
    const toId = String(connection.toId || "");

    let connectedId = "";
    if (fromId === item.id) {
      connectedId = toId;
    } else if (toId === item.id) {
      connectedId = fromId;
    }

    if (!connectedId || connectedId === excludeId || seen.has(connectedId)) {
      return;
    }

    const connectedItem = getItemById(connectedId);
    if (!connectedItem || connectedItem.id === item.id) {
      return;
    }

    seen.add(connectedId);
    connectedItems.push(connectedItem);
  });

  return connectedItems;
}

function getAncestorCanvasItems(item, options = {}) {
  const { excludeId = null } = options;
  const ancestors = [];

  let currentParentId = String(item?.parentId || "");
  while (currentParentId) {
    const parentItem = getItemById(currentParentId);
    if (!parentItem) {
      break;
    }

    if (parentItem.id !== excludeId) {
      ancestors.push(parentItem);
    }

    currentParentId = String(parentItem.parentId || "");
  }

  return ancestors;
}

function getContextualCanvasCandidates(item, predicate, options = {}) {
  const {
    excludeId = null,
    includeAncestors = true,
    includeConnections = true
  } = options;

  if (!item || typeof predicate !== "function") {
    return [];
  }

  const candidates = [];
  const seen = new Set();

  const addCandidate = (candidate) => {
    if (!candidate || candidate.id === item.id || candidate.id === excludeId || seen.has(candidate.id)) {
      return;
    }

    if (!predicate(candidate)) {
      return;
    }

    seen.add(candidate.id);
    candidates.push(candidate);
  };

  if (includeAncestors) {
    getAncestorCanvasItems(item, { excludeId }).forEach(addCandidate);
  }

  if (includeConnections) {
    getConnectedCanvasItems(item, { excludeId }).forEach(addCandidate);
  }

  return candidates;
}

function getPreferredContextualCandidate(item, predicate, currentRef = "", options = {}) {
  const candidates = getContextualCanvasCandidates(item, predicate, options);
  if (!candidates.length) {
    return null;
  }

  const currentRefId = String(currentRef || "").trim();
  if (currentRefId) {
    const currentMatch = candidates.find((candidate) => candidate.id === currentRefId);
    if (currentMatch) {
      return currentMatch;
    }
  }

  return candidates[0];
}

function resolveDnsParentZoneName(properties, options = {}) {
  const { excludeItemId = null } = options;
  const parentZoneMode = properties?.parentZoneMode === "existing" ? "existing" : "custom";

  if (parentZoneMode === "existing") {
    const parentZoneRef = String(properties?.parentZoneRef || "");
    const parentZoneItem = getItemById(parentZoneRef);
    if (parentZoneItem && parentZoneItem.id !== excludeItemId && isDnsZoneItem(parentZoneItem)) {
      return sanitizeDnsNameValue(parentZoneItem.name || properties?.parentZoneName || "");
    }
  }

  return sanitizeDnsNameValue(properties?.parentZoneName || "");
}

function applyDnsZoneEffectiveName(item) {
  if (!item || !isDnsZoneItem(item) || !item.properties || typeof item.properties !== "object") {
    return "";
  }

  const properties = item.properties;
  let resolvedParentZoneName = resolveDnsParentZoneName(properties, { excludeItemId: item.id });

  if (properties.parentZoneMode === "existing" && !resolvedParentZoneName) {
    properties.parentZoneMode = "custom";
    properties.parentZoneRef = "";
    resolvedParentZoneName = resolveDnsParentZoneName(properties, { excludeItemId: item.id });
  }

  if (properties.parentZoneMode === "existing" && resolvedParentZoneName) {
    properties.parentZoneName = resolvedParentZoneName;
  }

  const effectiveDnsName = buildDnsZoneEffectiveName(properties, {
    parentZoneName: resolvedParentZoneName
  });

  if (effectiveDnsName) {
    properties.dnsName = effectiveDnsName;
    item.name = effectiveDnsName;
  }

  return effectiveDnsName;
}

function sanitizeExistingCustomReference(modeValue, refValue, validItemIds = null) {
  let mode = modeValue === "existing" ? "existing" : "custom";
  let ref = String(refValue || "");

  if (validItemIds instanceof Set && (!ref || !validItemIds.has(ref))) {
    mode = "custom";
    ref = "";
  }

  return { mode, ref };
}

function sanitizeItemProperties(resourceType, properties, validItemIds = null, itemName = "") {
  const normalizedType = String(resourceType || "");
  const source = properties && typeof properties === "object" ? properties : {};
  const hasTags = Array.isArray(source.tags);
  const tags = sanitizeTagList(source.tags);

  if (isResourceGroupTypeName(normalizedType)) {
    return {
      location: String(source.location || ""),
      tags
    };
  }

  if (isDnsZoneTypeName(normalizedType)) {
    const dnsMode = source.dnsMode === "child" ? "child" : "root";
    const fallbackDnsName = sanitizeDnsNameValue(itemName);
    let dnsName = sanitizeDnsNameValue(source.dnsName || source.zoneName || "");
    const parentZoneSelection = sanitizeExistingCustomReference(source.parentZoneMode, source.parentZoneRef, validItemIds);
    let parentZoneMode = parentZoneSelection.mode;
    let parentZoneRef = parentZoneSelection.ref;
    let parentZoneName = sanitizeDnsNameValue(source.parentZoneName || "");
    let childDomainName = sanitizeDnsNameValue(source.childDomainName || source.childDomainLabel || "");
    const resourceGroupSelection = sanitizeExistingCustomReference(source.resourceGroupMode, source.resourceGroupRef, validItemIds);
    const resourceGroupMode = resourceGroupSelection.mode;
    const resourceGroupRef = resourceGroupSelection.ref;
    const resourceGroupName = String(source.resourceGroupName || "");

    if (!dnsName && fallbackDnsName && !isGeneratedResourceName(fallbackDnsName) && !isDnsZoneTypeName(fallbackDnsName)) {
      dnsName = fallbackDnsName;
    }

    if (dnsMode === "child" && !parentZoneName && !childDomainName && dnsName) {
      const splitName = splitDnsName(dnsName);
      parentZoneName = splitName.parentZoneName;
      childDomainName = splitName.childDomainName;
    }

    const effectiveDnsName = buildDnsZoneEffectiveName({
      dnsMode,
      dnsName,
      parentZoneName,
      childDomainName
    }, {
      parentZoneName
    });

    return {
      dnsMode,
      dnsName: sanitizeDnsNameValue(effectiveDnsName || dnsName),
      parentZoneMode,
      parentZoneRef,
      parentZoneName,
      childDomainName,
      resourceGroupMode,
      resourceGroupRef,
      resourceGroupName,
      tags
    };
  }

  if (isNetworkSecurityGroupTypeName(normalizedType)) {
    const resourceGroupSelection = sanitizeExistingCustomReference(source.resourceGroupMode, source.resourceGroupRef, validItemIds);
    const associatedSubnetSelection = sanitizeExistingCustomReference(
      source.associatedSubnetMode || source.subnetAssociationMode,
      source.associatedSubnetRef || source.subnetAssociationRef,
      validItemIds
    );
    return {
      resourceGroupMode: resourceGroupSelection.mode,
      resourceGroupRef: resourceGroupSelection.ref,
      resourceGroupName: String(source.resourceGroupName || ""),
      location: String(source.location || ""),
      associatedSubnetMode: associatedSubnetSelection.mode,
      associatedSubnetRef: associatedSubnetSelection.ref,
      associatedSubnetName: String(source.associatedSubnetName || source.subnetAssociationName || ""),
      securityRules: sanitizeNetworkSecurityRules(source.securityRules),
      tags
    };
  }

  if (isRouteTableTypeName(normalizedType)) {
    const resourceGroupSelection = sanitizeExistingCustomReference(source.resourceGroupMode, source.resourceGroupRef, validItemIds);
    const disableBgpRoutePropagation = source.disableBgpRoutePropagation === undefined
      ? !normalizeBooleanValue(source.propagateGatewayRoutes, true)
      : normalizeBooleanValue(source.disableBgpRoutePropagation, false);
    return {
      resourceGroupMode: resourceGroupSelection.mode,
      resourceGroupRef: resourceGroupSelection.ref,
      resourceGroupName: String(source.resourceGroupName || ""),
      location: String(source.location || ""),
      disableBgpRoutePropagation,
      routes: sanitizeRouteDefinitions(source.routes || source.routeDefinitions),
      tags
    };
  }

  if (isSubnetTypeName(normalizedType)) {
    const parentVirtualNetworkSelection = sanitizeExistingCustomReference(
      source.virtualNetworkMode || source.parentVirtualNetworkMode,
      source.virtualNetworkRef || source.parentVirtualNetworkRef,
      validItemIds
    );
    const networkSecurityGroupSelection = sanitizeExistingCustomReference(
      source.networkSecurityGroupMode || source.subnetNsgMode,
      source.networkSecurityGroupRef || source.subnetNsgRef,
      validItemIds
    );
    const routeTableSelection = sanitizeExistingCustomReference(
      source.routeTableMode || source.subnetRouteTableMode,
      source.routeTableRef || source.subnetRouteTableRef,
      validItemIds
    );
    const legacyPrivateEndpointPolicy = String(source.privateEndpointPolicy || "").toLowerCase();
    const privateEndpointNetworkPolicies = ["disabled", "enabled"].includes(String(source.privateEndpointNetworkPolicies || "").toLowerCase())
      ? String(source.privateEndpointNetworkPolicies || "").toLowerCase()
      : (["nsg", "route-table", "both"].includes(legacyPrivateEndpointPolicy) ? "enabled" : "disabled");
    const subnetPurpose = normalizeSubnetPurpose(source.subnetPurpose);

    return {
      subnetPurpose,
      subnetName: resolveSubnetNameForPurpose(subnetPurpose, source.subnetName),
      subnetPrivate: normalizeBooleanValue(source.subnetPrivate, false),
      virtualNetworkMode: parentVirtualNetworkSelection.mode,
      virtualNetworkRef: parentVirtualNetworkSelection.ref,
      virtualNetworkName: String(source.virtualNetworkName || source.parentVirtualNetworkName || ""),
      networkSecurityGroupMode: networkSecurityGroupSelection.mode,
      networkSecurityGroupRef: networkSecurityGroupSelection.ref,
      networkSecurityGroupName: String(source.networkSecurityGroupName || source.subnetNsgName || ""),
      routeTableMode: routeTableSelection.mode,
      routeTableRef: routeTableSelection.ref,
      routeTableName: String(source.routeTableName || source.subnetRouteTableName || ""),
      serviceEndpoints: sanitizeStringList(source.serviceEndpoints, {
        maxItems: 40,
        maxLength: 120,
        allowCsv: true,
        fallback: source.subnetServiceEndpoints
      }),
      delegations: sanitizeSubnetDelegations(source.delegations),
      privateEndpointNetworkPolicies,
      tags
    };
  }

  if (isVirtualNetworkTypeName(normalizedType)) {
    const resourceGroupSelection = sanitizeExistingCustomReference(source.resourceGroupMode, source.resourceGroupRef, validItemIds);
    const bastionPublicIpSelection = sanitizeExistingCustomReference(source.bastionPublicIpMode, source.bastionPublicIpRef, validItemIds);
    const firewallPublicIpSelection = sanitizeExistingCustomReference(source.firewallPublicIpMode, source.firewallPublicIpRef, validItemIds);

    const firewallTier = ["basic", "standard", "premium"].includes(String(source.firewallTier || "").toLowerCase())
      ? String(source.firewallTier || "").toLowerCase()
      : "basic";

    return {
      resourceGroupMode: resourceGroupSelection.mode,
      resourceGroupRef: resourceGroupSelection.ref,
      resourceGroupName: String(source.resourceGroupName || ""),
      location: String(source.location || ""),
      addressPrefixes: sanitizeStringList(source.addressPrefixes, {
        maxItems: 20,
        maxLength: 64,
        allowCsv: true,
        fallback: source.addressSpace || source.addressSpaceCidr || []
      }),
      dnsServers: sanitizeStringList(source.dnsServers, {
        maxItems: 20,
        maxLength: 64,
        allowCsv: true
      }),
      enableVirtualNetworkEncryption: normalizeBooleanValue(source.enableVirtualNetworkEncryption, false),
      enableAzureBastion: normalizeBooleanValue(source.enableAzureBastion, false),
      bastionName: String(source.bastionName || ""),
      bastionPublicIpMode: bastionPublicIpSelection.mode,
      bastionPublicIpRef: bastionPublicIpSelection.ref,
      bastionPublicIpName: String(source.bastionPublicIpName || ""),
      enableAzureFirewall: normalizeBooleanValue(source.enableAzureFirewall, false),
      firewallName: String(source.firewallName || ""),
      firewallTier,
      firewallPolicyName: String(source.firewallPolicyName || ""),
      firewallPublicIpMode: firewallPublicIpSelection.mode,
      firewallPublicIpRef: firewallPublicIpSelection.ref,
      firewallPublicIpName: String(source.firewallPublicIpName || ""),
      enableDdosProtection: normalizeBooleanValue(source.enableDdosProtection, false),
      tags
    };
  }

  if (isPublicIpTypeName(normalizedType)) {
    const resourceGroupSelection = sanitizeExistingCustomReference(source.resourceGroupMode, source.resourceGroupRef, validItemIds);
    const sku = String(source.sku || "standard").toLowerCase() === "standardv2" ? "standardv2" : "standard";
    const tier = sku === "standardv2"
      ? "regional"
      : (["regional", "global"].includes(String(source.tier || "").toLowerCase()) ? String(source.tier || "").toLowerCase() : "regional");
    const routingPreference = sku === "standardv2"
      ? "microsoft-network"
      : (["microsoft-network", "internet"].includes(String(source.routingPreference || "").toLowerCase()) ? String(source.routingPreference || "").toLowerCase() : "microsoft-network");
    const zone = ["zone-redundant", "1", "2", "3"].includes(String(source.zone || "").toLowerCase())
      ? String(source.zone || "").toLowerCase()
      : "zone-redundant";
    const ipVersion = String(source.ipVersion || "ipv4").toLowerCase() === "ipv6" ? "ipv6" : "ipv4";
    const dnsLabelScope = ["none", "no-reuse", "resource-group", "subscription", "tenant"].includes(String(source.dnsLabelScope || "").toLowerCase())
      ? String(source.dnsLabelScope || "").toLowerCase()
      : "none";
    const ddosProtection = ["network", "ip", "disabled"].includes(String(source.ddosProtection || "").toLowerCase())
      ? String(source.ddosProtection || "").toLowerCase()
      : "disabled";

    return {
      resourceGroupMode: resourceGroupSelection.mode,
      resourceGroupRef: resourceGroupSelection.ref,
      resourceGroupName: String(source.resourceGroupName || ""),
      location: String(source.location || ""),
      publicIPAllocationMethod: "static",
      ipVersion,
      sku,
      zone,
      tier,
      routingPreference,
      idleTimeoutMinutes: normalizeIntegerValue(source.idleTimeoutMinutes, 4, 4, 30),
      dnsLabel: String(source.dnsLabel || ""),
      dnsLabelScope,
      ddosProtection,
      tags
    };
  }

  if (hasTags) {
    return { tags };
  }

  return {};
}

function ensureItemProperties(item) {
  if (!item || typeof item !== "object") {
    return {};
  }

  item.properties = sanitizeItemProperties(item.resourceType || item.name, item.properties, null, item.name);

  if (isDnsZoneItem(item)) {
    applyDnsZoneEffectiveName(item);
  }

  return item.properties;
}

function getPreferredResourceGroup(parentContainer = null) {
  if (parentContainer && isResourceGroupItem(parentContainer)) {
    return parentContainer;
  }
  return getCanvasResourceGroups()[0] || null;
}

function getPreferredVirtualNetwork(parentContainer = null) {
  if (parentContainer && isVirtualNetworkItem(parentContainer)) {
    return parentContainer;
  }

  let ancestorId = String(parentContainer?.parentId || "");
  while (ancestorId) {
    const ancestorItem = getItemById(ancestorId);
    if (!ancestorItem) {
      break;
    }

    if (isVirtualNetworkItem(ancestorItem)) {
      return ancestorItem;
    }

    ancestorId = String(ancestorItem.parentId || "");
  }

  return getCanvasVirtualNetworks()[0] || null;
}

function getPreferredSubnet(parentContainer = null) {
  if (parentContainer && isSubnetItem(parentContainer)) {
    return parentContainer;
  }

  let ancestorId = String(parentContainer?.parentId || "");
  while (ancestorId) {
    const ancestorItem = getItemById(ancestorId);
    if (!ancestorItem) {
      break;
    }

    if (isSubnetItem(ancestorItem)) {
      return ancestorItem;
    }

    ancestorId = String(ancestorItem.parentId || "");
  }

  return getCanvasSubnets()[0] || null;
}

function buildDefaultResourceGroupBinding(parentContainer = null) {
  const preferredResourceGroup = getPreferredResourceGroup(parentContainer);
  const preferredResourceGroupProperties = preferredResourceGroup ? ensureItemProperties(preferredResourceGroup) : {};

  return {
    resourceGroupMode: preferredResourceGroup ? "existing" : "custom",
    resourceGroupRef: preferredResourceGroup ? preferredResourceGroup.id : "",
    resourceGroupName: preferredResourceGroup ? preferredResourceGroup.name : "",
    location: String(preferredResourceGroupProperties.location || "")
  };
}

function createDefaultItemProperties(resourceType, parentContainer = null) {
  if (isResourceGroupTypeName(resourceType)) {
    return {
      location: "",
      tags: []
    };
  }

  if (isDnsZoneTypeName(resourceType)) {
    const preferredResourceGroup = getPreferredResourceGroup(parentContainer);

    return {
      dnsMode: "root",
      dnsName: "",
      parentZoneMode: "custom",
      parentZoneRef: "",
      parentZoneName: "",
      childDomainName: "",
      resourceGroupMode: preferredResourceGroup ? "existing" : "custom",
      resourceGroupRef: preferredResourceGroup ? preferredResourceGroup.id : "",
      resourceGroupName: preferredResourceGroup ? preferredResourceGroup.name : "",
      tags: []
    };
  }

  if (isNetworkSecurityGroupTypeName(resourceType)) {
    const binding = buildDefaultResourceGroupBinding(parentContainer);
    const preferredSubnet = getPreferredSubnet(parentContainer);
    return {
      ...binding,
      associatedSubnetMode: preferredSubnet ? "existing" : "custom",
      associatedSubnetRef: preferredSubnet ? preferredSubnet.id : "",
      associatedSubnetName: preferredSubnet ? preferredSubnet.name : "",
      securityRules: [],
      tags: []
    };
  }

  if (isRouteTableTypeName(resourceType)) {
    const binding = buildDefaultResourceGroupBinding(parentContainer);
    return {
      ...binding,
      disableBgpRoutePropagation: false,
      routes: [],
      tags: []
    };
  }

  if (isVirtualNetworkTypeName(resourceType)) {
    const binding = buildDefaultResourceGroupBinding(parentContainer);
    return {
      ...binding,
      addressPrefixes: [],
      dnsServers: [],
      enableVirtualNetworkEncryption: false,
      enableAzureBastion: false,
      bastionName: "",
      bastionPublicIpMode: "custom",
      bastionPublicIpRef: "",
      bastionPublicIpName: "",
      enableAzureFirewall: false,
      firewallName: "",
      firewallTier: "basic",
      firewallPolicyName: "",
      firewallPublicIpMode: "custom",
      firewallPublicIpRef: "",
      firewallPublicIpName: "",
      enableDdosProtection: false,
      tags: []
    };
  }

  if (isSubnetTypeName(resourceType)) {
    const subnetPurpose = "default";
    const preferredVirtualNetwork = getPreferredVirtualNetwork(parentContainer);
    return {
      subnetPurpose,
      subnetName: resolveSubnetNameForPurpose(subnetPurpose),
      subnetPrivate: false,
      virtualNetworkMode: preferredVirtualNetwork ? "existing" : "custom",
      virtualNetworkRef: preferredVirtualNetwork ? preferredVirtualNetwork.id : "",
      virtualNetworkName: preferredVirtualNetwork ? preferredVirtualNetwork.name : "",
      networkSecurityGroupMode: "custom",
      networkSecurityGroupRef: "",
      networkSecurityGroupName: "",
      routeTableMode: "custom",
      routeTableRef: "",
      routeTableName: "",
      serviceEndpoints: [],
      delegations: [],
      privateEndpointNetworkPolicies: "disabled",
      tags: []
    };
  }

  if (isPublicIpTypeName(resourceType)) {
    const binding = buildDefaultResourceGroupBinding(parentContainer);
    return {
      ...binding,
      publicIPAllocationMethod: "static",
      ipVersion: "ipv4",
      sku: "standard",
      zone: "zone-redundant",
      tier: "regional",
      routingPreference: "microsoft-network",
      idleTimeoutMinutes: 4,
      dnsLabel: "",
      dnsLabelScope: "none",
      ddosProtection: "disabled",
      tags: []
    };
  }

  return {};
}

function assignExistingResourceGroup(item, resourceGroupItem) {
  if (!item || !resourceGroupItem || !supportsResourceGroupBinding(item) || !isResourceGroupItem(resourceGroupItem)) {
    return;
  }

  const properties = ensureItemProperties(item);
  properties.resourceGroupMode = "existing";
  properties.resourceGroupRef = resourceGroupItem.id;
  properties.resourceGroupName = resourceGroupItem.name;
}

function switchResourceGroupToCustom(item, fallbackName = "") {
  if (!item || !supportsResourceGroupBinding(item)) {
    return;
  }

  const properties = ensureItemProperties(item);
  const currentResourceGroup = getItemById(properties.resourceGroupRef);
  properties.resourceGroupMode = "custom";
  properties.resourceGroupRef = "";
  properties.resourceGroupName = String(fallbackName || currentResourceGroup?.name || properties.resourceGroupName || "");
}

function assignExistingParentDnsZone(item, parentZoneItem) {
  if (!item || !parentZoneItem || !isDnsZoneItem(item) || !isDnsZoneItem(parentZoneItem) || item.id === parentZoneItem.id) {
    return;
  }

  const properties = ensureItemProperties(item);
  properties.parentZoneMode = "existing";
  properties.parentZoneRef = parentZoneItem.id;
  properties.parentZoneName = sanitizeDnsNameValue(parentZoneItem.name || properties.parentZoneName || "");
}

function switchDnsParentZoneToCustom(item, fallbackName = "") {
  if (!item || !isDnsZoneItem(item)) {
    return;
  }

  const properties = ensureItemProperties(item);
  const currentParentZone = getItemById(properties.parentZoneRef);
  properties.parentZoneMode = "custom";
  properties.parentZoneRef = "";
  properties.parentZoneName = sanitizeDnsNameValue(fallbackName || currentParentZone?.name || properties.parentZoneName || "");
}

function assignExistingParentVirtualNetwork(item, virtualNetworkItem) {
  if (!item || !virtualNetworkItem || !isSubnetItem(item) || !isVirtualNetworkItem(virtualNetworkItem)) {
    return;
  }

  const properties = ensureItemProperties(item);
  properties.virtualNetworkMode = "existing";
  properties.virtualNetworkRef = virtualNetworkItem.id;
  properties.virtualNetworkName = String(virtualNetworkItem.name || properties.virtualNetworkName || "");
}

function switchSubnetParentVirtualNetworkToCustom(item, fallbackName = "") {
  if (!item || !isSubnetItem(item)) {
    return;
  }

  const properties = ensureItemProperties(item);
  const currentVirtualNetwork = getItemById(properties.virtualNetworkRef);
  properties.virtualNetworkMode = "custom";
  properties.virtualNetworkRef = "";
  properties.virtualNetworkName = String(fallbackName || currentVirtualNetwork?.name || properties.virtualNetworkName || "");
}

function getCanvasItemAndDescendants(rootItem) {
  if (!rootItem || !rootItem.id) {
    return [];
  }

  const descendants = [rootItem];
  const queue = [rootItem.id];

  while (queue.length) {
    const parentId = queue.shift();
    state.canvasItems.forEach((candidate) => {
      if (candidate.parentId !== parentId) {
        return;
      }

      descendants.push(candidate);
      queue.push(candidate.id);
    });
  }

  return descendants;
}

function applyAutoBindingsForItem(item) {
  if (!item) {
    return false;
  }

  const properties = ensureItemProperties(item);
  let changed = false;

  if (supportsResourceGroupBinding(item)) {
    const nextResourceGroup = getPreferredContextualCandidate(item, isResourceGroupItem, properties.resourceGroupRef);
    if (nextResourceGroup) {
      if (
        properties.resourceGroupMode !== "existing"
        || properties.resourceGroupRef !== nextResourceGroup.id
        || String(properties.resourceGroupName || "") !== String(nextResourceGroup.name || "")
      ) {
        assignExistingResourceGroup(item, nextResourceGroup);
        changed = true;
      }
    }
  }

  if (isDnsZoneItem(item)) {
    const nextParentZone = getPreferredContextualCandidate(item, isDnsZoneItem, properties.parentZoneRef);
    if (nextParentZone && nextParentZone.id !== item.id) {
      const nextParentZoneName = sanitizeDnsNameValue(nextParentZone.name || "");
      if (
        properties.parentZoneMode !== "existing"
        || properties.parentZoneRef !== nextParentZone.id
        || sanitizeDnsNameValue(properties.parentZoneName || "") !== nextParentZoneName
      ) {
        assignExistingParentDnsZone(item, nextParentZone);
        changed = true;
      }
    }

    const previousDnsName = String(item.name || "");
    const effectiveDnsName = applyDnsZoneEffectiveName(item);
    if (effectiveDnsName && effectiveDnsName !== previousDnsName) {
      changed = true;
    }
  }

  if (isSubnetItem(item)) {
    const nextParentVirtualNetwork = getPreferredContextualCandidate(item, isVirtualNetworkItem, properties.virtualNetworkRef);
    if (nextParentVirtualNetwork) {
      if (
        properties.virtualNetworkMode !== "existing"
        || properties.virtualNetworkRef !== nextParentVirtualNetwork.id
        || String(properties.virtualNetworkName || "") !== String(nextParentVirtualNetwork.name || "")
      ) {
        assignExistingParentVirtualNetwork(item, nextParentVirtualNetwork);
        changed = true;
      }
    }

    const nextNetworkSecurityGroup = getPreferredContextualCandidate(item, isNetworkSecurityGroupItem, properties.networkSecurityGroupRef);
    if (nextNetworkSecurityGroup) {
      if (
        properties.networkSecurityGroupMode !== "existing"
        || properties.networkSecurityGroupRef !== nextNetworkSecurityGroup.id
        || String(properties.networkSecurityGroupName || "") !== String(nextNetworkSecurityGroup.name || "")
      ) {
        assignExistingDependency(
          properties,
          "networkSecurityGroupMode",
          "networkSecurityGroupRef",
          "networkSecurityGroupName",
          nextNetworkSecurityGroup
        );
        changed = true;
      }
    }

    const nextRouteTable = getPreferredContextualCandidate(item, isRouteTableItem, properties.routeTableRef);
    if (nextRouteTable) {
      if (
        properties.routeTableMode !== "existing"
        || properties.routeTableRef !== nextRouteTable.id
        || String(properties.routeTableName || "") !== String(nextRouteTable.name || "")
      ) {
        assignExistingDependency(properties, "routeTableMode", "routeTableRef", "routeTableName", nextRouteTable);
        changed = true;
      }
    }
  }

  if (isNetworkSecurityGroupItem(item)) {
    const nextSubnet = getPreferredContextualCandidate(item, isSubnetItem, properties.associatedSubnetRef);
    if (nextSubnet) {
      if (
        properties.associatedSubnetMode !== "existing"
        || properties.associatedSubnetRef !== nextSubnet.id
        || String(properties.associatedSubnetName || "") !== String(nextSubnet.name || "")
      ) {
        assignExistingDependency(properties, "associatedSubnetMode", "associatedSubnetRef", "associatedSubnetName", nextSubnet);
        changed = true;
      }
    }
  }

  if (isVirtualNetworkItem(item)) {
    const nextBastionPublicIp = getPreferredContextualCandidate(item, isPublicIpItem, properties.bastionPublicIpRef);
    if (nextBastionPublicIp) {
      if (
        properties.bastionPublicIpMode !== "existing"
        || properties.bastionPublicIpRef !== nextBastionPublicIp.id
        || String(properties.bastionPublicIpName || "") !== String(nextBastionPublicIp.name || "")
      ) {
        assignExistingDependency(properties, "bastionPublicIpMode", "bastionPublicIpRef", "bastionPublicIpName", nextBastionPublicIp);
        changed = true;
      }
    }

    const nextFirewallPublicIp = getPreferredContextualCandidate(item, isPublicIpItem, properties.firewallPublicIpRef);
    if (nextFirewallPublicIp) {
      if (
        properties.firewallPublicIpMode !== "existing"
        || properties.firewallPublicIpRef !== nextFirewallPublicIp.id
        || String(properties.firewallPublicIpName || "") !== String(nextFirewallPublicIp.name || "")
      ) {
        assignExistingDependency(properties, "firewallPublicIpMode", "firewallPublicIpRef", "firewallPublicIpName", nextFirewallPublicIp);
        changed = true;
      }
    }
  }

  return changed;
}

function applyAutoBindingsForItemAndDescendants(rootItem) {
  if (!rootItem) {
    return false;
  }

  let changed = false;
  const scopedItems = getCanvasItemAndDescendants(rootItem);
  scopedItems.forEach((candidate) => {
    if (applyAutoBindingsForItem(candidate)) {
      changed = true;
    }
  });

  return changed;
}

function isContainerResource(resource) {
  const name = normalizeLabel(resource?.name);
  const containerTerms = [
    "management group",
    "management groups",
    "subscription",
    "subscriptions",
    "resource group",
    "resource groups",
    "virtual network",
    "virtual networks",
    "vnet",
    "subnet",
    "subnets"
  ];

  return containerTerms.some((term) => name.includes(term)) || name === "network";
}

function isLogicalOnlyContainer(item) {
  const name = normalizeLabel(item?.resourceType || item?.name);
  const logicalTerms = ["management group", "subscription", "resource group"];
  return logicalTerms.some((term) => name.includes(term));
}

function getVisualLayer(item) {
  if (!item) return 999;
  
  const name = normalizeLabel(item?.resourceType || item?.name);
  
  // Visual layers from back (0) to front (higher numbers)
  // Lower number = renders first = appears behind
  if (name.includes("management group")) return 0;
  if (name.includes("subscription")) return 1;
  if (name.includes("resource group")) return 2;
  if (name.includes("virtual network") || name.includes("vnet") || name === "network") return 3;
  if (name.includes("subnet")) return 4;
  
  // All other resources appear on top
  return 5;
}

function isConnectableItem(item) {
  if (!item) {
    return false;
  }

  if (item.isContainer && isLogicalOnlyContainer(item)) {
    return false;
  }

  return true;
}

// ===== Utility Functions =====
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

function getProjectPrefix(cloud) {
  return `${cloud}-`;
}

function getMaxSuffixLength(cloud) {
  return Math.max(1, MAX_PROJECT_NAME_LENGTH - getProjectPrefix(cloud).length);
}

function splitProjectName(cloud, fullName) {
  const safeCloud = ["Azure", "AWS", "GCP"].includes(cloud) ? cloud : "Azure";
  const prefix = getProjectPrefix(safeCloud);
  let suffix = (fullName || "").trim();

  if (suffix.toLowerCase().startsWith(prefix.toLowerCase())) {
    suffix = suffix.slice(prefix.length);
  }

  suffix = suffix.replace(/^[-\s]+/, "").trim();
  suffix = suffix.slice(0, getMaxSuffixLength(cloud));

  return {
    prefix,
    suffix
  };
}

function getNextResourceDefaultName() {
  const pattern = /^resource\s+(\d+)$/i;
  let maxIndex = 0;

  const allNames = [
    ...state.canvasItems.map((item) => item?.name),
    ...state.canvasConnections.map((connection) => connection?.name)
  ];

  allNames.forEach((name) => {
    const match = pattern.exec(String(name || "").trim());
    if (!match) {
      return;
    }
    maxIndex = Math.max(maxIndex, Number(match[1]) || 0);
  });

  return `Resource ${maxIndex + 1}`;
}

function renderIacIcon() {
  if (!projectIacIconEl) {
    return;
  }
  const lang = String(state.currentProject?.iacLanguage || "bicep").trim().toLowerCase();
  const src = IAC_ICONS[lang] || IAC_ICONS.bicep;
  const label = lang === "terraform" ? "Terraform" : lang === "opentofu" ? "OpenTofu" : "Bicep";
  projectIacIconEl.innerHTML = `<img src="${src}" alt="${label}" title="${label}" width="22" height="22" />`;
}

function renderProjectName() {
  if (!state.currentProject) {
    return;
  }

  const { prefix, suffix } = splitProjectName(state.currentProject.cloud, state.currentProject.name);
  if (projectNamePrefixDisplay) {
    projectNamePrefixDisplay.textContent = prefix;
  }
  if (projectNameDisplay) {
    projectNameDisplay.textContent = suffix;
  }
  state.currentProject.name = `${prefix}${suffix}`;
}

function placeCaretAtEnd(element) {
  const selection = window.getSelection?.();
  if (!selection) {
    return;
  }

  const range = document.createRange();
  range.selectNodeContents(element);
  range.collapse(false);
  selection.removeAllRanges();
  selection.addRange(range);
}

function sanitizeProject(project) {
  if (!project || typeof project !== "object") {
    return null;
  }

  const cloud = ["Azure", "AWS", "GCP"].includes(project.cloud) ? project.cloud : null;
  if (!cloud || !project.id) {
    return null;
  }

  const { prefix, suffix } = splitProjectName(cloud, project.name);

  const incomingView = project.canvasView && typeof project.canvasView === "object"
    ? project.canvasView
    : {};
  const incomingItems = Array.isArray(project.canvasItems)
    ? project.canvasItems
    : [];
  const incomingConnections = Array.isArray(project.canvasConnections)
    ? project.canvasConnections
    : [];

  const sanitizedItems = incomingItems
    .map((item, index) => {
      const resourceType = String(item.resourceType || item.type || item.name || "Resource");
      return {
        id: String(item.id || `item-${Date.now()}`),
        name: String(item.name || `Resource ${index + 1}`),
        resourceType,
        iconSrc: String(item.iconSrc || ""),
        category: String(item.category || ""),
        isContainer: Boolean(item.isContainer),
        parentId: item.parentId ? String(item.parentId) : null,
        width: clamp(Number(item.width) || CANVAS_CONTAINER.defaultWidth, CANVAS_CONTAINER.minWidth, 2400),
        height: clamp(Number(item.height) || CANVAS_CONTAINER.defaultHeight, CANVAS_CONTAINER.minHeight, 2400),
        x: Number(item.x) || 0,
        y: Number(item.y) || 0,
        properties: sanitizeItemProperties(resourceType, item.properties, null, item.name)
      };
    })
    .filter((item) => item.iconSrc);

  const validItemIds = new Set(sanitizedItems.map((item) => item.id));
  sanitizedItems.forEach((item) => {
    item.properties = sanitizeItemProperties(item.resourceType, item.properties, validItemIds, item.name);
    syncSubnetItemName(item);
  });
  const pattern = /^resource\s+(\d+)$/i;
  let maxResourceIndex = 0;
  sanitizedItems.forEach((item) => {
    const match = pattern.exec(String(item.name || "").trim());
    if (match) {
      maxResourceIndex = Math.max(maxResourceIndex, Number(match[1]) || 0);
    }
  });

  return {
    ...project,
    cloud,
    name: `${prefix}${suffix}`,
    lastSaved: Number(project.lastSaved) || Date.now(),
    canvasStateHash: String(project.canvasStateHash || "").trim(),
    canvasView: {
      x: Number(incomingView.x) || 0,
      y: Number(incomingView.y) || 0,
      zoom: clamp(Number(incomingView.zoom) || 1, CANVAS_ZOOM.min, CANVAS_ZOOM.max)
    },
    canvasItems: sanitizedItems,
    canvasConnections: incomingConnections
      .map((connection) => {
        const incomingName = String(connection.name || "").trim();
        const isLegacyArrowName = incomingName.includes("→");
        const hasExplicitName = incomingName.length > 0 && !isLegacyArrowName;
        const name = hasExplicitName ? incomingName : `Resource ${++maxResourceIndex}`;
        return {
          id: String(connection.id || `conn-${Date.now()}`),
          name,
          fromId: String(connection.fromId || ""),
          toId: String(connection.toId || ""),
          direction: connection.direction === "bi" ? "bi" : "one-way",
          sourceAnchor: ["top", "right", "bottom", "left"].includes(connection.sourceAnchor) ? connection.sourceAnchor : "right",
          targetAnchor: ["top", "right", "bottom", "left"].includes(connection.targetAnchor) ? connection.targetAnchor : "left"
        };
      })
      .filter((connection) => connection.fromId && connection.toId && validItemIds.has(connection.fromId) && validItemIds.has(connection.toId))
  };
}

function saveCurrentProject() {
  if (state.currentProject) {
    const projectId = state.currentProject.id;
    const idx = state.projects.findIndex((p) => p.id === projectId);
    if (idx !== -1) {
      state.projects[idx] = state.currentProject;
    }
  }
}

function formatExportTimestamp(date = new Date()) {
  const pad = (value) => String(value).padStart(2, "0");
  return `${date.getFullYear()}-${pad(date.getMonth() + 1)}-${pad(date.getDate())}-${pad(date.getHours())}-${pad(date.getMinutes())}-${pad(date.getSeconds())}`;
}

function sanitizeFileSegment(value, fallback = "project") {
  const sanitized = String(value || "")
    .trim()
    .replace(/[^a-zA-Z0-9._-]+/g, "-")
    .replace(/^[-._]+|[-._]+$/g, "");
  return sanitized || fallback;
}

function getItemExportSize(item) {
  if (item.isContainer) {
    return {
      width: Number(item.width) || CANVAS_CONTAINER.defaultWidth,
      height: Number(item.height) || CANVAS_CONTAINER.defaultHeight,
    };
  }

  const nodeEl = canvasLayerEl?.querySelector(`.canvas-node[data-item-id="${item.id}"]`);
  if (nodeEl) {
    const rect = nodeEl.getBoundingClientRect();
    const zoom = Number(state.canvasView.zoom) || 1;
    const width = Math.max(180, rect.width / zoom);
    const height = Math.max(74, rect.height / zoom);
    return { width, height };
  }

  return { width: 180, height: 74 };
}

function getAnchorPointForExport(itemRect, anchor = "right") {
  const cx = itemRect.x + itemRect.width / 2;
  const cy = itemRect.y + itemRect.height / 2;

  if (anchor === "top") {
    return { x: cx, y: itemRect.y };
  }
  if (anchor === "bottom") {
    return { x: cx, y: itemRect.y + itemRect.height };
  }
  if (anchor === "left") {
    return { x: itemRect.x, y: cy };
  }
  return { x: itemRect.x + itemRect.width, y: cy };
}

function buildExportImageMap(items) {
  const sources = [...new Set(items.map((item) => String(item.iconSrc || "").trim()).filter(Boolean))];
  const imageEntries = sources.map((src) => new Promise((resolve) => {
    const image = new Image();
    image.onload = () => resolve([src, image]);
    image.onerror = () => resolve([src, null]);
    image.src = src;
  }));

  return Promise.all(imageEntries).then((entries) => new Map(entries));
}

function drawRoundedRect(ctx, x, y, width, height, radius) {
  const r = Math.min(radius, width / 2, height / 2);
  ctx.beginPath();
  ctx.moveTo(x + r, y);
  ctx.lineTo(x + width - r, y);
  ctx.quadraticCurveTo(x + width, y, x + width, y + r);
  ctx.lineTo(x + width, y + height - r);
  ctx.quadraticCurveTo(x + width, y + height, x + width - r, y + height);
  ctx.lineTo(x + r, y + height);
  ctx.quadraticCurveTo(x, y + height, x, y + height - r);
  ctx.lineTo(x, y + r);
  ctx.quadraticCurveTo(x, y, x + r, y);
  ctx.closePath();
}

function drawTextEllipsis(ctx, text, x, y, maxWidth) {
  const value = String(text || "");
  if (ctx.measureText(value).width <= maxWidth) {
    ctx.fillText(value, x, y);
    return;
  }

  const ellipsis = "…";
  let trimmed = value;
  while (trimmed.length > 0 && ctx.measureText(trimmed + ellipsis).width > maxWidth) {
    trimmed = trimmed.slice(0, -1);
  }
  ctx.fillText(trimmed + ellipsis, x, y);
}

function drawArrowHead(ctx, from, to, color) {
  const dx = to.x - from.x;
  const dy = to.y - from.y;
  const length = Math.hypot(dx, dy);
  if (!length) {
    return;
  }

  const ux = dx / length;
  const uy = dy / length;
  const size = 9;
  const angle = Math.PI / 6;

  const leftX = to.x - size * (ux * Math.cos(angle) + uy * Math.sin(angle));
  const leftY = to.y - size * (uy * Math.cos(angle) - ux * Math.sin(angle));
  const rightX = to.x - size * (ux * Math.cos(angle) - uy * Math.sin(angle));
  const rightY = to.y - size * (uy * Math.cos(angle) + ux * Math.sin(angle));

  ctx.fillStyle = color;
  ctx.beginPath();
  ctx.moveTo(to.x, to.y);
  ctx.lineTo(leftX, leftY);
  ctx.lineTo(rightX, rightY);
  ctx.closePath();
  ctx.fill();
}

async function exportCurrentDiagram(format = "png") {
  if (!state.currentProject) {
    return;
  }

  const itemRects = new Map();
  state.canvasItems.forEach((item) => {
    const world = getItemWorldPosition(item.id);
    const size = getItemExportSize(item);
    itemRects.set(item.id, {
      x: world.x,
      y: world.y,
      width: size.width,
      height: size.height,
      item,
    });
  });

  const fallbackBounds = { minX: 0, minY: 0, maxX: 900, maxY: 560 };
  const bounds = { minX: Number.POSITIVE_INFINITY, minY: Number.POSITIVE_INFINITY, maxX: Number.NEGATIVE_INFINITY, maxY: Number.NEGATIVE_INFINITY };

  const includePoint = (x, y) => {
    bounds.minX = Math.min(bounds.minX, x);
    bounds.minY = Math.min(bounds.minY, y);
    bounds.maxX = Math.max(bounds.maxX, x);
    bounds.maxY = Math.max(bounds.maxY, y);
  };

  itemRects.forEach((rect) => {
    includePoint(rect.x, rect.y);
    includePoint(rect.x + rect.width, rect.y + rect.height);
  });

  state.canvasConnections.forEach((connection) => {
    const fromRect = itemRects.get(connection.fromId);
    const toRect = itemRects.get(connection.toId);
    if (!fromRect || !toRect) {
      return;
    }
    const from = getAnchorPointForExport(fromRect, connection.sourceAnchor || "right");
    const to = getAnchorPointForExport(toRect, connection.targetAnchor || "left");
    includePoint(from.x, from.y);
    includePoint(to.x, to.y);
  });

  if (!Number.isFinite(bounds.minX) || !Number.isFinite(bounds.minY) || !Number.isFinite(bounds.maxX) || !Number.isFinite(bounds.maxY)) {
    bounds.minX = fallbackBounds.minX;
    bounds.minY = fallbackBounds.minY;
    bounds.maxX = fallbackBounds.maxX;
    bounds.maxY = fallbackBounds.maxY;
  }

  const padding = 48;
  const exportWidth = Math.max(320, Math.ceil(bounds.maxX - bounds.minX + padding * 2));
  const exportHeight = Math.max(220, Math.ceil(bounds.maxY - bounds.minY + padding * 2));

  const pixelRatio = Math.min(window.devicePixelRatio || 1, 2);
  const canvas = document.createElement("canvas");
  canvas.width = Math.ceil(exportWidth * pixelRatio);
  canvas.height = Math.ceil(exportHeight * pixelRatio);

  const ctx = canvas.getContext("2d");
  if (!ctx) {
    throw new Error("Unable to initialize export canvas");
  }

  ctx.scale(pixelRatio, pixelRatio);

  const colors = {
    bg: getComputedStyle(document.documentElement).getPropertyValue("--bg").trim() || "#000000",
    panel: getComputedStyle(document.documentElement).getPropertyValue("--panel").trim() || "#1a1a1a",
    border: getComputedStyle(document.documentElement).getPropertyValue("--border").trim() || "#333333",
    accent: getComputedStyle(document.documentElement).getPropertyValue("--accent").trim() || "#3b82f6",
  };

  const worldOffsetX = bounds.minX - padding;
  const worldOffsetY = bounds.minY - padding;

  const toCanvasX = (worldX) => worldX - worldOffsetX;
  const toCanvasY = (worldY) => worldY - worldOffsetY;

  ctx.fillStyle = colors.bg;
  ctx.fillRect(0, 0, exportWidth, exportHeight);

  const gridSize = 40;
  const gridOffsetX = ((-worldOffsetX % gridSize) + gridSize) % gridSize;
  const gridOffsetY = ((-worldOffsetY % gridSize) + gridSize) % gridSize;
  ctx.strokeStyle = colors.border;
  ctx.globalAlpha = 0.3;
  ctx.lineWidth = 1;
  for (let x = gridOffsetX; x <= exportWidth; x += gridSize) {
    ctx.beginPath();
    ctx.moveTo(x, 0);
    ctx.lineTo(x, exportHeight);
    ctx.stroke();
  }
  for (let y = gridOffsetY; y <= exportHeight; y += gridSize) {
    ctx.beginPath();
    ctx.moveTo(0, y);
    ctx.lineTo(exportWidth, y);
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  ctx.lineWidth = 2;
  ctx.strokeStyle = colors.accent;
  state.canvasConnections.forEach((connection) => {
    const fromRect = itemRects.get(connection.fromId);
    const toRect = itemRects.get(connection.toId);
    if (!fromRect || !toRect) {
      return;
    }

    const from = getAnchorPointForExport(fromRect, connection.sourceAnchor || "right");
    const to = getAnchorPointForExport(toRect, connection.targetAnchor || "left");
    const start = { x: toCanvasX(from.x), y: toCanvasY(from.y) };
    const end = { x: toCanvasX(to.x), y: toCanvasY(to.y) };

    ctx.beginPath();
    ctx.moveTo(start.x, start.y);
    ctx.lineTo(end.x, end.y);
    ctx.stroke();

    drawArrowHead(ctx, start, end, colors.accent);
    if (connection.direction === "bi") {
      drawArrowHead(ctx, end, start, colors.accent);
    }
  });

  const imageMap = await buildExportImageMap(state.canvasItems);
  const sortedItems = [...state.canvasItems].sort((first, second) => getVisualLayer(first) - getVisualLayer(second));

  sortedItems.forEach((item) => {
    const rect = itemRects.get(item.id);
    if (!rect) {
      return;
    }

    const x = toCanvasX(rect.x);
    const y = toCanvasY(rect.y);
    const width = rect.width;
    const height = rect.height;
    const headerHeight = 32;
    const icon = imageMap.get(String(item.iconSrc || ""));
    const resourceType = String(item.resourceType || item.name || "Resource");
    const resourceName = String(item.name || "Resource");

    if (item.isContainer) {
      drawRoundedRect(ctx, x, y, width, height, 8);
      ctx.fillStyle = "rgba(47, 79, 79, 0.5)";
      ctx.fill();
      ctx.lineWidth = 2;
      ctx.strokeStyle = "#253F3F";
      ctx.setLineDash([6, 4]);
      ctx.stroke();
      ctx.setLineDash([]);

      ctx.fillStyle = colors.panel;
      ctx.fillRect(x, y, width, headerHeight);
      ctx.strokeStyle = colors.border;
      ctx.setLineDash([4, 3]);
      ctx.beginPath();
      ctx.moveTo(x, y + headerHeight);
      ctx.lineTo(x + width, y + headerHeight);
      ctx.stroke();
      ctx.setLineDash([]);

      if (icon) {
        ctx.drawImage(icon, x + 10, y + 8, 16, 16);
      }

      ctx.fillStyle = "#ffffff";
      ctx.font = "600 12px Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
      drawTextEllipsis(ctx, `${resourceType}: ${resourceName}`, x + 32, y + 20, width - 64);
      return;
    }

    drawRoundedRect(ctx, x, y, width, height, 8);
    ctx.fillStyle = "#2F4F4F";
    ctx.fill();
    ctx.lineWidth = 2;
    ctx.strokeStyle = "#253F3F";
    ctx.stroke();

    ctx.fillStyle = colors.panel;
    ctx.fillRect(x, y, width, headerHeight);
    ctx.strokeStyle = colors.border;
    ctx.setLineDash([4, 3]);
    ctx.beginPath();
    ctx.moveTo(x, y + headerHeight);
    ctx.lineTo(x + width, y + headerHeight);
    ctx.stroke();
    ctx.setLineDash([]);

    if (icon) {
      ctx.drawImage(icon, x + 10, y + 8, 16, 16);
    }

    ctx.fillStyle = "#ffffff";
    ctx.font = "600 12px Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    drawTextEllipsis(ctx, resourceType, x + 32, y + 20, width - 64);

    ctx.font = "600 12px Inter, -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif";
    drawTextEllipsis(ctx, resourceName, x + 10, y + 51, width - 20);
  });

  const normalizedFormat = format === "jpeg" ? "jpeg" : "png";
  const mimeType = normalizedFormat === "jpeg" ? "image/jpeg" : "image/png";
  const imageData = canvas.toDataURL(mimeType, 0.92);

  const response = await fetch("/api/project/export-diagram", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
    },
    body: JSON.stringify({
      projectId: state.currentProject.id,
      projectName: state.currentProject.name,
      format: normalizedFormat,
      imageData,
    }),
  });

  if (!response.ok) {
    let message = "Unable to export diagram";
    try {
      const errorPayload = await response.json();
      if (errorPayload?.detail) {
        message = String(errorPayload.detail);
      }
    } catch {
      // Ignore parse failures and use fallback error message.
    }
    throw new Error(message);
  }

  const payload = await response.json();
  const savedPath = payload?.path ? String(payload.path) : "Diagram folder";
  setSaveStatus(`Exported to ${savedPath}`);
}

async function loadCurrentProject(projectId) {
  try {
    const response = await fetch(`/api/project/${encodeURIComponent(projectId)}`, { cache: "no-store" });
    if (!response.ok) {
      return false;
    }

    const payload = await response.json();
    const project = payload?.project && typeof payload.project === "object" ? payload.project : {};
    const canvasState = payload?.canvasState && typeof payload.canvasState === "object" ? payload.canvasState : {};

    const normalized = sanitizeProject({
      ...project,
      canvasStateHash: payload?.stateHash,
      leftWidth: canvasState.leftWidth,
      rightWidth: canvasState.rightWidth,
      bottomHeight: canvasState.bottomHeight,
      bottomRightWidth: canvasState.bottomRightWidth,
      selectedResource: canvasState.selectedResource,
      searchTerm: canvasState.searchTerm,
      canvasView: canvasState.canvasView,
      canvasItems: canvasState.canvasItems,
      canvasConnections: canvasState.canvasConnections
    });

    if (!normalized) {
      return false;
    }

    state.currentProject = { ...normalized };
    return true;
  } catch {
    return false;
  }
}

// ===== Catalog Loading =====
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

// ===== Resource Rendering =====
function updatePropertyPanel(resourceName) {
  propertyPanelRenderedStateKey = "";

  if (!resourceName) {
    setSelectedResourceName("None selected");
    propertyContentEl.textContent = "Select a resource or connection to edit property details.";
    return;
  }

  setSelectedResourceName(resourceName);
  propertyContentEl.innerHTML = [
    "Property",
    "- Name",
    "- Region",
    "- SKU / Tier",
    "- Tags"
  ].join("<br />");
}

function buildResourceMetaMarkup() {
  return "";
}

function buildResourceActionMarkup() {
  return [
    "<div class=\"property-actions\">",
    `<button class="btn btn--sm btn--primary" type="button" data-property-action="save">Save</button>`,
    "<button class=\"btn btn--sm btn--danger\" type=\"button\" data-resource-action=\"remove\">Remove</button>",
    "</div>"
  ].join("");
}

function buildTagEditorMarkup(tags) {
  const editableRows = buildEditableTagRows(tags);
  const tagRows = editableRows
    .map((tag, index) => [
      '<div class="tag-row tag-row--key-value">',
      `<input class="property-input" type="text" placeholder="&lt;Key&gt;" value="${escapeHtml(tag.key)}" data-resource-tag-field="key" data-tag-index="${index}" maxlength="128" />`,
      '<span class="tag-row__separator">:</span>',
      `<input class="property-input" type="text" placeholder="&lt;value&gt;" value="${escapeHtml(tag.value)}" data-resource-tag-field="value" data-tag-index="${index}" maxlength="256" />`,
      "</div>"
    ].join(""))
    .join("");

  return [
    '<div class="property-row property-row--tags">',
    '<span class="property-label">Tags:</span>',
    `<div class="tag-list">${tagRows}</div>`,
    "</div>"
  ].join("");
}

function resolveExistingCustomReferenceState(modeValue, refValue, items) {
  const safeItems = Array.isArray(items) ? items : [];
  const hasItems = safeItems.length > 0;
  const effectiveMode = modeValue === "existing" && hasItems ? "existing" : "custom";
  const selectedItem = effectiveMode === "existing"
    ? safeItems.find((item) => item.id === refValue) || safeItems[0] || null
    : null;

  return {
    safeItems,
    hasItems,
    effectiveMode,
    selectedItem
  };
}

function buildExistingCustomReferenceMarkup(config) {
  const {
    sourceLabel,
    modeField,
    modeValue,
    refField,
    refValue,
    nameField,
    nameValue,
    existingItems,
    existingLabel,
    customLabel,
    customPlaceholder = "",
    customMaxLength = 120,
    emptyExistingMessage = "No compatible resource is on the canvas yet. Switch to Custom to type a name."
  } = config;

  const referenceState = resolveExistingCustomReferenceState(modeValue, refValue, existingItems);
  const existingOptions = referenceState.safeItems
    .map((item) => `<option value="${escapeHtml(item.id)}" ${referenceState.selectedItem?.id === item.id ? "selected" : ""}>${escapeHtml(item.name)}</option>`)
    .join("");

  const markup = [
    '<label class="property-row">',
    `<span class="property-label">${escapeHtml(sourceLabel)}</span>`,
    `<select class="property-input" data-resource-field="${escapeHtml(modeField)}"><option value="existing" ${referenceState.effectiveMode === "existing" ? "selected" : ""} ${referenceState.hasItems ? "" : "disabled"}>Use existing on canvas</option><option value="custom" ${referenceState.effectiveMode === "custom" ? "selected" : ""}>Custom</option></select>`,
    '</label>',
    referenceState.effectiveMode === "existing"
      ? [
          '<label class="property-row">',
          `<span class="property-label">${escapeHtml(existingLabel)}</span>`,
          existingOptions
            ? `<select class="property-input" data-resource-field="${escapeHtml(refField)}">${existingOptions}</select>`
            : `<div class="property-helper">${escapeHtml(emptyExistingMessage)}</div>`,
          '</label>'
        ].join("")
      : [
          '<label class="property-row">',
          `<span class="property-label">${escapeHtml(customLabel)}</span>`,
          `<input class="property-input" type="text" value="${escapeHtml(nameValue || "")}" data-resource-field="${escapeHtml(nameField)}" placeholder="${escapeHtml(customPlaceholder)}" maxlength="${Number.parseInt(customMaxLength, 10) || 120}" />`,
          '</label>'
        ].join("")
  ].join("");

  return {
    ...referenceState,
    markup
  };
}

function buildGenericResourcePropertyMarkup(selectedItem) {
  const basicSectionContent = [
    '<label class="property-row">',
    '<span class="property-label">Name</span>',
    `<input class="property-input" type="text" value="${escapeHtml(selectedItem.name)}" data-resource-field="name" maxlength="80" />`,
    '</label>'
  ].join("");

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildResourceGroupPropertyMarkup(selectedItem) {
  const properties = ensureItemProperties(selectedItem);

  const basicSectionContent = [
    '<label class="property-row">',
    '<span class="property-label">Resource Group Name</span>',
    `<input class="property-input" type="text" value="${escapeHtml(selectedItem.name)}" data-resource-field="name" maxlength="80" />`,
    '</label>',
    buildLocationInputMarkup(properties.location),
    buildTagEditorMarkup(properties.tags)
  ].join("");

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildLocationInputMarkup(value, fieldName = "location") {
  return [
    '<label class="property-row">',
    '<span class="property-label">Location</span>',
    `<input class="property-input" type="text" value="${escapeHtml(value || "")}" data-resource-field="${escapeHtml(fieldName)}" placeholder="e.g. westeurope" maxlength="60" />`,
    '</label>'
  ].join("");
}

function buildBooleanSelectMarkup(label, fieldName, value, trueLabel = "Enabled", falseLabel = "Disabled") {
  const normalizedValue = normalizeBooleanValue(value, false);
  return [
    '<label class="property-row">',
    `<span class="property-label">${escapeHtml(label)}</span>`,
    `<select class="property-input" data-resource-field="${escapeHtml(fieldName)}"><option value="true" ${normalizedValue ? "selected" : ""}>${escapeHtml(trueLabel)}</option><option value="false" ${normalizedValue ? "" : "selected"}>${escapeHtml(falseLabel)}</option></select>`,
    '</label>'
  ].join("");
}

function buildPropertySectionMarkup(title, content, options = {}) {
  const { open = false } = options;
  return [
    `<details class="property-group"${open ? " open" : ""}>`,
    `<summary class="property-group__summary">${escapeHtml(title)}</summary>`,
    `<div class="property-group__body">${content}</div>`,
    '</details>'
  ].join("");
}

function buildStringListEditorMarkup(config) {
  const {
    label,
    fieldName,
    values,
    placeholder = "",
    addLabel = "Add",
    emptyMessage = "No items configured.",
    maxLength = 120
  } = config;

  const safeValues = Array.isArray(values) ? values : [];
  const rows = safeValues
    .map((value, index) => [
      '<div class="tag-row">',
      `<input class="property-input" type="text" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(value)}" data-string-list-field="${escapeHtml(fieldName)}" data-list-index="${index}" maxlength="${Number.parseInt(maxLength, 10) || 120}" />`,
      '<span></span>',
      `<button class="btn btn--sm btn--danger" type="button" data-list-action="remove-string" data-list-field="${escapeHtml(fieldName)}" data-list-index="${index}">Remove</button>`,
      '</div>'
    ].join(""))
    .join("");

  return [
    '<div class="property-row property-row--tags">',
    '<div class="property-inline-header">',
    `<span class="property-label">${escapeHtml(label)}</span>`,
    `<button class="btn btn--sm btn--secondary" type="button" data-list-action="add-string" data-list-field="${escapeHtml(fieldName)}">${escapeHtml(addLabel)}</button>`,
    '</div>',
    safeValues.length
      ? `<div class="tag-list">${rows}</div>`
      : `<div class="property-helper">${escapeHtml(emptyMessage)}</div>`,
    '</div>'
  ].join("");
}

function buildAutoGrowingStringListMarkup(config) {
  const {
    label,
    fieldName,
    values,
    placeholder = "",
    maxLength = 120
  } = config;

  const editableRows = buildEditableStringRows(values, {
    maxItems: 40,
    maxLength,
    allowCsv: true
  });

  const rows = editableRows
    .map((value, index) => [
      '<div class="tag-row tag-row--single">',
      `<input class="property-input" type="text" placeholder="${escapeHtml(placeholder)}" value="${escapeHtml(value)}" data-subnet-list-field="${escapeHtml(fieldName)}" data-list-index="${index}" maxlength="${Number.parseInt(maxLength, 10) || 120}" />`,
      '</div>'
    ].join(""))
    .join("");

  return [
    '<div class="property-row property-row--tags">',
    `<span class="property-label">${escapeHtml(label)}:</span>`,
    `<div class="tag-list">${rows}</div>`,
    '</div>'
  ].join("");
}

function buildSubnetServiceEndpointsEditorMarkup(serviceEndpoints) {
  return buildAutoGrowingStringListMarkup({
    label: "Service Endpoint",
    fieldName: "serviceEndpoints",
    values: serviceEndpoints,
    placeholder: "e.g. Microsoft.Storage",
    maxLength: 120
  });
}

function buildRouteDefinitionsEditorMarkup(routes) {
  const safeRoutes = Array.isArray(routes) ? routes : [];
  const rows = safeRoutes
    .map((route, index) => [
      '<details class="property-subsection">',
      `<summary class="property-subsection__summary">${escapeHtml(String(route.name || `route-${index + 1}`))}</summary>`,
      '<div class="property-subsection__body">',
      '<label class="property-row">',
      '<span class="property-label">Route Name:</span>',
      `<input class="property-input" type="text" placeholder="Route name" value="${escapeHtml(route.name || "")}" data-object-list-field="routes" data-object-key="name" data-list-index="${index}" maxlength="80" />`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Address Prefix:</span>',
      `<input class="property-input" type="text" placeholder="Address prefix" value="${escapeHtml(route.addressPrefix || "")}" data-object-list-field="routes" data-object-key="addressPrefix" data-list-index="${index}" maxlength="64" />`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Next Hop Type:</span>',
      `<select class="property-input" data-object-list-field="routes" data-object-key="nextHopType" data-list-index="${index}"><option value="virtual-network-gateway" ${route.nextHopType === "virtual-network-gateway" ? "selected" : ""}>Virtual Network Gateway</option><option value="vnet-local" ${route.nextHopType === "vnet-local" ? "selected" : ""}>VNet Local</option><option value="internet" ${route.nextHopType === "internet" ? "selected" : ""}>Internet</option><option value="virtual-appliance" ${route.nextHopType === "virtual-appliance" ? "selected" : ""}>Virtual Appliance</option><option value="none" ${route.nextHopType === "none" ? "selected" : ""}>None</option></select>`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Next Hop IP Address:</span>',
      `<input class="property-input" type="text" placeholder="Next hop IP (optional)" value="${escapeHtml(route.nextHopIpAddress || "")}" data-object-list-field="routes" data-object-key="nextHopIpAddress" data-list-index="${index}" maxlength="64" />`,
      '</label>',
      '<div class="property-inline-header">',
      `<span class="property-helper">Route ${index + 1}</span>`,
      `<button class="btn btn--sm btn--danger" type="button" data-list-action="remove-object" data-list-field="routes" data-list-index="${index}">Remove Route</button>`,
      '</div>',
      '</div>',
      '</details>'
    ].join(""))
    .join("");

  return [
    '<div class="property-row property-row--tags">',
    '<div class="property-inline-header">',
    '<span class="property-label">Routes</span>',
    '<button class="btn btn--sm btn--secondary" type="button" data-list-action="add-object" data-list-field="routes">Add Route</button>',
    '</div>',
    safeRoutes.length
      ? `<div class="tag-list">${rows}</div>`
      : '<div class="property-helper">No routes configured.</div>',
    '</div>'
  ].join("");
}

function buildSubnetDelegationsEditorMarkup(delegations) {
  const delegationServices = extractDelegationServiceNames(delegations);
  return buildAutoGrowingStringListMarkup({
    label: "Delegation",
    fieldName: "delegationServices",
    values: delegationServices,
    placeholder: "e.g. Microsoft.ContainerInstance/containerGroups",
    maxLength: 120
  });
}

function buildNetworkSecurityRuleMarkup(securityRules) {
  const safeRules = Array.isArray(securityRules) ? securityRules : [];
  const rows = safeRules
    .map((rule, index) => [
      '<details class="property-subsection">',
      `<summary class="property-subsection__summary">${escapeHtml(String(rule.name || `rule-${index + 1}`))}</summary>`,
      '<div class="property-subsection__body">',
      '<label class="property-row">',
      '<span class="property-label">Rule Name:</span>',
      `<input class="property-input" type="text" placeholder="Rule name" value="${escapeHtml(rule.name || "")}" data-object-list-field="securityRules" data-object-key="name" data-list-index="${index}" maxlength="80" />`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Priority:</span>',
      `<input class="property-input" type="number" min="100" max="4096" step="1" value="${escapeHtml(String(rule.priority ?? ""))}" data-object-list-field="securityRules" data-object-key="priority" data-list-index="${index}" />`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Direction:</span>',
      `<select class="property-input" data-object-list-field="securityRules" data-object-key="direction" data-list-index="${index}"><option value="inbound" ${rule.direction === "inbound" ? "selected" : ""}>Inbound</option><option value="outbound" ${rule.direction === "outbound" ? "selected" : ""}>Outbound</option></select>`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Access:</span>',
      `<select class="property-input" data-object-list-field="securityRules" data-object-key="access" data-list-index="${index}"><option value="allow" ${rule.access === "allow" ? "selected" : ""}>Allow</option><option value="deny" ${rule.access === "deny" ? "selected" : ""}>Deny</option></select>`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Protocol:</span>',
      `<select class="property-input" data-object-list-field="securityRules" data-object-key="protocol" data-list-index="${index}"><option value="tcp" ${rule.protocol === "tcp" ? "selected" : ""}>TCP</option><option value="udp" ${rule.protocol === "udp" ? "selected" : ""}>UDP</option><option value="icmp" ${rule.protocol === "icmp" ? "selected" : ""}>ICMP</option><option value="any" ${rule.protocol === "any" ? "selected" : ""}>Any</option></select>`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Source Address Prefix:</span>',
      `<input class="property-input" type="text" placeholder="Source address prefix" value="${escapeHtml(rule.sourceAddressPrefix || "*")}" data-object-list-field="securityRules" data-object-key="sourceAddressPrefix" data-list-index="${index}" maxlength="120" />`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Source Port Range:</span>',
      `<input class="property-input" type="text" placeholder="Source port range" value="${escapeHtml(rule.sourcePortRange || "*")}" data-object-list-field="securityRules" data-object-key="sourcePortRange" data-list-index="${index}" maxlength="40" />`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Destination Address Prefix:</span>',
      `<input class="property-input" type="text" placeholder="Destination address prefix" value="${escapeHtml(rule.destinationAddressPrefix || "*")}" data-object-list-field="securityRules" data-object-key="destinationAddressPrefix" data-list-index="${index}" maxlength="120" />`,
      '</label>',
      '<label class="property-row">',
      '<span class="property-label">Destination Port Range:</span>',
      `<input class="property-input" type="text" placeholder="Destination port range" value="${escapeHtml(rule.destinationPortRange || "*")}" data-object-list-field="securityRules" data-object-key="destinationPortRange" data-list-index="${index}" maxlength="40" />`,
      '</label>',
      '<div class="property-inline-header">',
      `<span class="property-helper">Rule ${index + 1}</span>`,
      `<button class="btn btn--sm btn--danger" type="button" data-list-action="remove-object" data-list-field="securityRules" data-list-index="${index}">Remove Rule</button>`,
      '</div>',
      '</div>',
      '</details>'
    ].join(""))
    .join("");

  return [
    '<div class="property-row property-row--tags">',
    '<div class="property-inline-header">',
    '<span class="property-label">Security Rules</span>',
    '<button class="btn btn--sm btn--secondary" type="button" data-list-action="add-object" data-list-field="securityRules">Add Rule</button>',
    '</div>',
    safeRules.length
      ? `<div class="tag-list">${rows}</div>`
      : '<div class="property-helper">No security rules configured.</div>',
    '</div>'
  ].join("");
}

function createDefaultObjectListItem(fieldName, index = 0) {
  if (fieldName === "routes") {
    return {
      name: `route-${index + 1}`,
      addressPrefix: "0.0.0.0/0",
      nextHopType: "internet",
      nextHopIpAddress: ""
    };
  }

  if (fieldName === "delegations") {
    return {
      name: `delegation-${index + 1}`,
      serviceName: ""
    };
  }

  if (fieldName === "securityRules") {
    return {
      name: `rule-${index + 1}`,
      priority: clamp(100 + (index * 10), 100, 4096),
      direction: "inbound",
      access: "allow",
      protocol: "tcp",
      sourceAddressPrefix: "*",
      sourcePortRange: "*",
      destinationAddressPrefix: "*",
      destinationPortRange: "*"
    };
  }

  return {};
}

function ensureStringListField(properties, fieldName) {
  if (!properties || !fieldName) {
    return [];
  }

  if (!Array.isArray(properties[fieldName])) {
    properties[fieldName] = [];
  }

  return properties[fieldName];
}

function ensureObjectListField(properties, fieldName) {
  if (!properties || !fieldName) {
    return [];
  }

  if (!Array.isArray(properties[fieldName])) {
    properties[fieldName] = [];
  }

  return properties[fieldName];
}

function normalizeObjectListInputValue(fieldName, key, value, fallback, index = 0) {
  if (fieldName === "routes") {
    if (key === "nextHopType") {
      return normalizeRouteNextHopType(value);
    }

    if (key === "name") {
      const normalizedName = String(value || "").trim().slice(0, 80);
      return normalizedName || String(fallback || `route-${index + 1}`);
    }

    if (key === "addressPrefix") {
      const addressPrefix = String(value || "").trim().slice(0, 64);
      return addressPrefix || String(fallback || "0.0.0.0/0");
    }

    if (key === "nextHopIpAddress") {
      return String(value || "").trim().slice(0, 64);
    }
  }

  if (fieldName === "delegations") {
    if (key === "name") {
      const normalizedName = String(value || "").trim().slice(0, 80);
      return normalizedName || String(fallback || `delegation-${index + 1}`);
    }

    if (key === "serviceName") {
      return String(value || "").trim().slice(0, 120);
    }
  }

  if (fieldName === "securityRules") {
    if (key === "priority") {
      return normalizeIntegerValue(value, normalizeIntegerValue(fallback, 100, 100, 4096), 100, 4096);
    }

    if (key === "direction") {
      return normalizeSecurityRuleDirection(value);
    }

    if (key === "access") {
      return normalizeSecurityRuleAccess(value);
    }

    if (key === "protocol") {
      return normalizeSecurityRuleProtocol(value);
    }

    if (key === "name") {
      const normalizedName = String(value || "").trim().slice(0, 80);
      return normalizedName || String(fallback || `rule-${index + 1}`);
    }

    if (["sourceAddressPrefix", "sourcePortRange", "destinationAddressPrefix", "destinationPortRange"].includes(key)) {
      return String(value || "").trim().slice(0, 120) || String(fallback || "*");
    }
  }

  return String(value || "");
}

function buildNetworkSecurityGroupPropertyMarkup(selectedItem) {
  const properties = ensureItemProperties(selectedItem);
  const resourceGroupReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Resource Group Source",
    modeField: "resourceGroupMode",
    modeValue: properties.resourceGroupMode,
    refField: "resourceGroupRef",
    refValue: properties.resourceGroupRef,
    nameField: "resourceGroupName",
    nameValue: properties.resourceGroupName,
    existingItems: getCanvasResourceGroups({ excludeId: selectedItem.id }),
    existingLabel: "Resource Group",
    customLabel: "Resource Group Name",
    customPlaceholder: "Enter resource group name",
    customMaxLength: 90,
    emptyExistingMessage: "No Resource Group is on the canvas yet. Switch to Custom to type a name."
  });

  const associatedSubnetReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Associated Subnet Source",
    modeField: "associatedSubnetMode",
    modeValue: properties.associatedSubnetMode,
    refField: "associatedSubnetRef",
    refValue: properties.associatedSubnetRef,
    nameField: "associatedSubnetName",
    nameValue: properties.associatedSubnetName,
    existingItems: getCanvasSubnets({ excludeId: selectedItem.id }),
    existingLabel: "Associated Subnet",
    customLabel: "Associated Subnet Name",
    customPlaceholder: "Enter subnet name",
    customMaxLength: 80,
    emptyExistingMessage: "No Subnet is on the canvas yet. Switch to Custom to type a name."
  });

  const basicSectionContent = [
    resourceGroupReference.markup,
    '<label class="property-row">',
    '<span class="property-label">Network Security Group Name</span>',
    `<input class="property-input" type="text" value="${escapeHtml(selectedItem.name)}" data-resource-field="name" maxlength="80" />`,
    '</label>',
    buildLocationInputMarkup(properties.location),
    associatedSubnetReference.markup,
    buildTagEditorMarkup(properties.tags)
  ].join("");

  const additionalSectionContent = buildNetworkSecurityRuleMarkup(properties.securityRules);

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildPropertySectionMarkup("Additional", additionalSectionContent),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildRouteTablePropertyMarkup(selectedItem) {
  const properties = ensureItemProperties(selectedItem);
  const resourceGroupReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Resource Group Source",
    modeField: "resourceGroupMode",
    modeValue: properties.resourceGroupMode,
    refField: "resourceGroupRef",
    refValue: properties.resourceGroupRef,
    nameField: "resourceGroupName",
    nameValue: properties.resourceGroupName,
    existingItems: getCanvasResourceGroups({ excludeId: selectedItem.id }),
    existingLabel: "Resource Group",
    customLabel: "Resource Group Name",
    customPlaceholder: "Enter resource group name",
    customMaxLength: 90,
    emptyExistingMessage: "No Resource Group is on the canvas yet. Switch to Custom to type a name."
  });

  const basicSectionContent = [
    resourceGroupReference.markup,
    '<label class="property-row">',
    '<span class="property-label">Route Table Name</span>',
    `<input class="property-input" type="text" value="${escapeHtml(selectedItem.name)}" data-resource-field="name" maxlength="80" />`,
    '</label>',
    buildLocationInputMarkup(properties.location),
    buildTagEditorMarkup(properties.tags)
  ].join("");

  const additionalSectionContent = [
    buildBooleanSelectMarkup(
      "Disable BGP Route Propagation",
      "disableBgpRoutePropagation",
      properties.disableBgpRoutePropagation,
      "Yes",
      "No"
    ),
    buildRouteDefinitionsEditorMarkup(properties.routes)
  ].join("");

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildPropertySectionMarkup("Additional", additionalSectionContent),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildVirtualNetworkPropertyMarkup(selectedItem) {
  const properties = ensureItemProperties(selectedItem);
  const resourceGroupReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Resource Group Source",
    modeField: "resourceGroupMode",
    modeValue: properties.resourceGroupMode,
    refField: "resourceGroupRef",
    refValue: properties.resourceGroupRef,
    nameField: "resourceGroupName",
    nameValue: properties.resourceGroupName,
    existingItems: getCanvasResourceGroups({ excludeId: selectedItem.id }),
    existingLabel: "Resource Group",
    customLabel: "Resource Group Name",
    customPlaceholder: "Enter resource group name",
    customMaxLength: 90,
    emptyExistingMessage: "No Resource Group is on the canvas yet. Switch to Custom to type a name."
  });

  const bastionPublicIpReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Bastion Public IP Source",
    modeField: "bastionPublicIpMode",
    modeValue: properties.bastionPublicIpMode,
    refField: "bastionPublicIpRef",
    refValue: properties.bastionPublicIpRef,
    nameField: "bastionPublicIpName",
    nameValue: properties.bastionPublicIpName,
    existingItems: getCanvasPublicIps({ excludeId: selectedItem.id }),
    existingLabel: "Bastion Public IP",
    customLabel: "Bastion Public IP Name",
    customPlaceholder: "Enter Bastion public IP name",
    customMaxLength: 80,
    emptyExistingMessage: "No Public IP resource is on the canvas yet. Switch to Custom to type a name."
  });

  const firewallPublicIpReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Firewall Public IP Source",
    modeField: "firewallPublicIpMode",
    modeValue: properties.firewallPublicIpMode,
    refField: "firewallPublicIpRef",
    refValue: properties.firewallPublicIpRef,
    nameField: "firewallPublicIpName",
    nameValue: properties.firewallPublicIpName,
    existingItems: getCanvasPublicIps({ excludeId: selectedItem.id }),
    existingLabel: "Firewall Public IP",
    customLabel: "Firewall Public IP Name",
    customPlaceholder: "Enter Firewall public IP name",
    customMaxLength: 80,
    emptyExistingMessage: "No Public IP resource is on the canvas yet. Switch to Custom to type a name."
  });

  const addressPrefixesValue = Array.isArray(properties.addressPrefixes)
    ? properties.addressPrefixes.join(", ")
    : "";
  const dnsServersValue = Array.isArray(properties.dnsServers)
    ? properties.dnsServers.join(", ")
    : "";

  const basicSectionContent = [
    resourceGroupReference.markup,
    '<label class="property-row">',
    '<span class="property-label">Virtual Network Name</span>',
    `<input class="property-input" type="text" value="${escapeHtml(selectedItem.name)}" data-resource-field="name" maxlength="80" />`,
    '</label>',
    buildLocationInputMarkup(properties.location),
    '<label class="property-row">',
    '<span class="property-label">Address Prefix</span>',
    `<input class="property-input" type="text" value="${escapeHtml(addressPrefixesValue)}" data-resource-field="addressPrefixes" placeholder="e.g. 10.0.0.0/16, 10.1.0.0/16" maxlength="400" />`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">DNS Server</span>',
    `<input class="property-input" type="text" value="${escapeHtml(dnsServersValue)}" data-resource-field="dnsServers" placeholder="e.g. 10.0.0.4, 10.0.0.5" maxlength="400" />`,
    '</label>',
    buildTagEditorMarkup(properties.tags)
  ].join("");

  const additionalSectionContent = [
    buildBooleanSelectMarkup("Virtual Network Encryption", "enableVirtualNetworkEncryption", properties.enableVirtualNetworkEncryption),
    buildBooleanSelectMarkup("Enable DDoS Protection", "enableDdosProtection", properties.enableDdosProtection),
    buildBooleanSelectMarkup("Enable Azure Bastion", "enableAzureBastion", properties.enableAzureBastion),
    properties.enableAzureBastion
      ? [
          '<label class="property-row">',
          '<span class="property-label">Bastion Name</span>',
          `<input class="property-input" type="text" value="${escapeHtml(properties.bastionName)}" data-resource-field="bastionName" maxlength="80" />`,
          '</label>',
          bastionPublicIpReference.markup
        ].join("")
      : "",
    buildBooleanSelectMarkup("Enable Azure Firewall", "enableAzureFirewall", properties.enableAzureFirewall),
    properties.enableAzureFirewall
      ? [
          '<label class="property-row">',
          '<span class="property-label">Firewall Name</span>',
          `<input class="property-input" type="text" value="${escapeHtml(properties.firewallName)}" data-resource-field="firewallName" maxlength="80" />`,
          '</label>',
          '<label class="property-row">',
          '<span class="property-label">Firewall Tier</span>',
          `<select class="property-input" data-resource-field="firewallTier"><option value="basic" ${properties.firewallTier === "basic" ? "selected" : ""}>Basic</option><option value="standard" ${properties.firewallTier === "standard" ? "selected" : ""}>Standard</option><option value="premium" ${properties.firewallTier === "premium" ? "selected" : ""}>Premium</option></select>`,
          '</label>',
          '<label class="property-row">',
          '<span class="property-label">Firewall Policy Name</span>',
          `<input class="property-input" type="text" value="${escapeHtml(properties.firewallPolicyName)}" data-resource-field="firewallPolicyName" maxlength="80" />`,
          '</label>',
          firewallPublicIpReference.markup
        ].join("")
      : ""
  ].join("");

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildPropertySectionMarkup("Additional", additionalSectionContent),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildSubnetPropertyMarkup(selectedItem) {
  const properties = ensureItemProperties(selectedItem);
  const subnetPurpose = normalizeSubnetPurpose(properties.subnetPurpose);
  const subnetName = resolveSubnetNameForPurpose(subnetPurpose, properties.subnetName);
  const subnetNameLocked = isSubnetNameLocked(subnetPurpose);

  properties.subnetPurpose = subnetPurpose;
  properties.subnetName = subnetName;

  const subnetNsgReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Subnet NSG Source",
    modeField: "networkSecurityGroupMode",
    modeValue: properties.networkSecurityGroupMode,
    refField: "networkSecurityGroupRef",
    refValue: properties.networkSecurityGroupRef,
    nameField: "networkSecurityGroupName",
    nameValue: properties.networkSecurityGroupName,
    existingItems: getCanvasNetworkSecurityGroups({ excludeId: selectedItem.id }),
    existingLabel: "Subnet Network Security Group",
    customLabel: "Subnet Network Security Group Name",
    customPlaceholder: "Enter NSG name",
    customMaxLength: 80,
    emptyExistingMessage: "No Network Security Group is on the canvas yet. Switch to Custom to type a name."
  });

  const parentVirtualNetworkReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Virtual Network Source",
    modeField: "virtualNetworkMode",
    modeValue: properties.virtualNetworkMode,
    refField: "virtualNetworkRef",
    refValue: properties.virtualNetworkRef,
    nameField: "virtualNetworkName",
    nameValue: properties.virtualNetworkName,
    existingItems: getCanvasVirtualNetworks({ excludeId: selectedItem.id }),
    existingLabel: "Virtual Network",
    customLabel: "Virtual Network Name",
    customPlaceholder: "Enter virtual network name",
    customMaxLength: 80,
    emptyExistingMessage: "No Virtual Network is on the canvas yet. Switch to Custom to type a name."
  });

  const subnetRouteTableReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Subnet Route Table Source",
    modeField: "routeTableMode",
    modeValue: properties.routeTableMode,
    refField: "routeTableRef",
    refValue: properties.routeTableRef,
    nameField: "routeTableName",
    nameValue: properties.routeTableName,
    existingItems: getCanvasRouteTables({ excludeId: selectedItem.id }),
    existingLabel: "Subnet Route Table",
    customLabel: "Subnet Route Table Name",
    customPlaceholder: "Enter route table name",
    customMaxLength: 80,
    emptyExistingMessage: "No Route Table is on the canvas yet. Switch to Custom to type a name."
  });

  const basicSectionContent = [
    '<label class="property-row">',
    '<span class="property-label">Subnet Type</span>',
    `<select class="property-input" data-resource-field="subnetPurpose"><option value="default" ${subnetPurpose === "default" ? "selected" : ""}>Default</option><option value="bastion" ${subnetPurpose === "bastion" ? "selected" : ""}>Bastion</option><option value="firewall" ${subnetPurpose === "firewall" ? "selected" : ""}>Firewall</option><option value="firewall-management" ${subnetPurpose === "firewall-management" ? "selected" : ""}>Firewall Management</option><option value="virtual-network-gateway" ${subnetPurpose === "virtual-network-gateway" ? "selected" : ""}>Virtual Network Gateway</option><option value="route-server" ${subnetPurpose === "route-server" ? "selected" : ""}>Route Server</option></select>`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">Subnet Name</span>',
    `<input class="property-input" type="text" value="${escapeHtml(subnetName)}" data-resource-field="subnetName" maxlength="80" ${subnetNameLocked ? "disabled" : ""} />`,
    '</label>',
    subnetNameLocked
      ? '<div class="property-helper">This subnet type uses a predefined Azure subnet name.</div>'
      : '',
    parentVirtualNetworkReference.markup,
    buildBooleanSelectMarkup("Private Subnet", "subnetPrivate", properties.subnetPrivate, "Yes", "No"),
    subnetNsgReference.markup,
    subnetRouteTableReference.markup,
    buildTagEditorMarkup(properties.tags)
  ].join("");

  const additionalSectionContent = [
    buildSubnetServiceEndpointsEditorMarkup(properties.serviceEndpoints),
    buildSubnetDelegationsEditorMarkup(properties.delegations),
    '<label class="property-row">',
    '<span class="property-label">Private Endpoint Network Policies</span>',
    `<select class="property-input" data-resource-field="privateEndpointNetworkPolicies"><option value="disabled" ${properties.privateEndpointNetworkPolicies === "disabled" ? "selected" : ""}>Disabled</option><option value="enabled" ${properties.privateEndpointNetworkPolicies === "enabled" ? "selected" : ""}>Enabled</option></select>`,
    '</label>'
  ].join("");

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildPropertySectionMarkup("Additional", additionalSectionContent),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildPublicIpPropertyMarkup(selectedItem) {
  const properties = ensureItemProperties(selectedItem);
  const resourceGroupReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Resource Group Source",
    modeField: "resourceGroupMode",
    modeValue: properties.resourceGroupMode,
    refField: "resourceGroupRef",
    refValue: properties.resourceGroupRef,
    nameField: "resourceGroupName",
    nameValue: properties.resourceGroupName,
    existingItems: getCanvasResourceGroups({ excludeId: selectedItem.id }),
    existingLabel: "Resource Group",
    customLabel: "Resource Group Name",
    customPlaceholder: "Enter resource group name",
    customMaxLength: 90,
    emptyExistingMessage: "No Resource Group is on the canvas yet. Switch to Custom to type a name."
  });

  const effectiveSku = properties.sku === "standardv2" ? "standardv2" : "standard";
  const isStandardV2 = effectiveSku === "standardv2";

  const basicSectionContent = [
    resourceGroupReference.markup,
    '<label class="property-row">',
    '<span class="property-label">Public IP Name</span>',
    `<input class="property-input" type="text" value="${escapeHtml(selectedItem.name)}" data-resource-field="name" maxlength="80" />`,
    '</label>',
    buildLocationInputMarkup(properties.location),
    '<label class="property-row">',
    '<span class="property-label">Allocation Method</span>',
    '<input class="property-input" type="text" value="Static" data-resource-field="publicIPAllocationMethod" disabled />',
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">IP Version</span>',
    `<select class="property-input" data-resource-field="ipVersion"><option value="ipv4" ${properties.ipVersion === "ipv4" ? "selected" : ""}>IPv4</option><option value="ipv6" ${properties.ipVersion === "ipv6" ? "selected" : ""}>IPv6</option></select>`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">SKU</span>',
    `<select class="property-input" data-resource-field="sku"><option value="standard" ${effectiveSku === "standard" ? "selected" : ""}>Standard</option><option value="standardv2" ${effectiveSku === "standardv2" ? "selected" : ""}>StandardV2</option></select>`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">Zone</span>',
    `<select class="property-input" data-resource-field="zone"><option value="zone-redundant" ${properties.zone === "zone-redundant" ? "selected" : ""}>Zone-redundant</option><option value="1" ${properties.zone === "1" ? "selected" : ""}>Zone 1</option><option value="2" ${properties.zone === "2" ? "selected" : ""}>Zone 2</option><option value="3" ${properties.zone === "3" ? "selected" : ""}>Zone 3</option></select>`,
    '</label>',
    buildTagEditorMarkup(properties.tags)
  ].join("");

  const additionalSectionContent = [
    '<label class="property-row">',
    '<span class="property-label">Tier</span>',
    `<select class="property-input" data-resource-field="tier" ${isStandardV2 ? "disabled" : ""}><option value="regional" ${properties.tier === "regional" ? "selected" : ""}>Regional</option><option value="global" ${properties.tier === "global" ? "selected" : ""}>Global</option></select>`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">Routing Preference</span>',
    `<select class="property-input" data-resource-field="routingPreference" ${isStandardV2 ? "disabled" : ""}><option value="microsoft-network" ${properties.routingPreference === "microsoft-network" ? "selected" : ""}>Microsoft Network</option><option value="internet" ${properties.routingPreference === "internet" ? "selected" : ""}>Internet</option></select>`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">Idle Timeout (minutes)</span>',
    `<input class="property-input" type="number" min="4" max="30" step="1" value="${escapeHtml(String(properties.idleTimeoutMinutes || 4))}" data-resource-field="idleTimeoutMinutes" />`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">DNS Label</span>',
    `<input class="property-input" type="text" value="${escapeHtml(properties.dnsLabel)}" data-resource-field="dnsLabel" maxlength="80" />`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">DNS Label Scope Reuse</span>',
    `<select class="property-input" data-resource-field="dnsLabelScope"><option value="none" ${properties.dnsLabelScope === "none" ? "selected" : ""}>None</option><option value="no-reuse" ${properties.dnsLabelScope === "no-reuse" ? "selected" : ""}>No Reuse</option><option value="resource-group" ${properties.dnsLabelScope === "resource-group" ? "selected" : ""}>Resource Group</option><option value="subscription" ${properties.dnsLabelScope === "subscription" ? "selected" : ""}>Subscription</option><option value="tenant" ${properties.dnsLabelScope === "tenant" ? "selected" : ""}>Tenant</option></select>`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">DDoS Protection Mode</span>',
    `<select class="property-input" data-resource-field="ddosProtection"><option value="disabled" ${properties.ddosProtection === "disabled" ? "selected" : ""}>Disabled</option><option value="network" ${properties.ddosProtection === "network" ? "selected" : ""}>Inherit Network</option><option value="ip" ${properties.ddosProtection === "ip" ? "selected" : ""}>IP level</option></select>`,
    '</label>'
  ].join("");

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildPropertySectionMarkup("Additional", additionalSectionContent),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildDnsZonePropertyMarkup(selectedItem) {
  const properties = ensureItemProperties(selectedItem);
  const resourceGroupReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Resource Group Source",
    modeField: "resourceGroupMode",
    modeValue: properties.resourceGroupMode,
    refField: "resourceGroupRef",
    refValue: properties.resourceGroupRef,
    nameField: "resourceGroupName",
    nameValue: properties.resourceGroupName,
    existingItems: getCanvasResourceGroups({ excludeId: selectedItem.id }),
    existingLabel: "Resource Group",
    customLabel: "Resource Group Name",
    customPlaceholder: "Enter resource group name",
    customMaxLength: 90,
    emptyExistingMessage: "No Resource Group is on the canvas yet. Switch to Custom to type a name."
  });

  const parentZoneReference = buildExistingCustomReferenceMarkup({
    sourceLabel: "Parent Zone Source",
    modeField: "parentZoneMode",
    modeValue: properties.parentZoneMode,
    refField: "parentZoneRef",
    refValue: properties.parentZoneRef,
    nameField: "parentZoneName",
    nameValue: properties.parentZoneName,
    existingItems: getCanvasDnsZones({ excludeId: selectedItem.id }),
    existingLabel: "Parent Zone",
    customLabel: "Parent Zone Name",
    customPlaceholder: "e.g. sharma.not",
    customMaxLength: 253,
    emptyExistingMessage: "No DNS Zone is on the canvas yet. Switch to Custom to type a parent zone."
  });

  const resolvedParentZoneName = parentZoneReference.effectiveMode === "existing"
    ? sanitizeDnsNameValue(parentZoneReference.selectedItem?.name || "")
    : sanitizeDnsNameValue(properties.parentZoneName || "");
  const effectiveDnsName = buildDnsZoneEffectiveName(properties, {
    parentZoneName: resolvedParentZoneName
  });

  const basicSectionContent = [
    resourceGroupReference.markup,
    '<label class="property-row">',
    `<span class="property-label">${properties.dnsMode === "child" ? "Child Domain Name" : "DNS Name"}</span>`,
    `<input class="property-input" type="text" value="${escapeHtml(properties.dnsMode === "child" ? properties.childDomainName : properties.dnsName)}" data-resource-field="${properties.dnsMode === "child" ? "childDomainName" : "dnsName"}" placeholder="${properties.dnsMode === "child" ? "e.g. mohit" : "e.g. sharma.not"}" maxlength="253" />`,
    '</label>',
    '<label class="property-row">',
    '<span class="property-label">DNS Type</span>',
    `<select class="property-input" data-resource-field="dnsMode"><option value="root" ${properties.dnsMode === "child" ? "" : "selected"}>Root zone</option><option value="child" ${properties.dnsMode === "child" ? "selected" : ""}>Child zone</option></select>`,
    '</label>',
    properties.dnsMode === "child"
      ? [
          parentZoneReference.markup
        ].join("")
      : '',
    `<div class="property-row"><span class="property-label">Effective DNS Name</span><span class="property-value">${escapeHtml(effectiveDnsName || "Not set")}</span></div>`,
    buildTagEditorMarkup(properties.tags)
  ].join("");

  return [
    '<div class="property-form">',
    buildPropertySectionMarkup("Basic", basicSectionContent, { open: true }),
    buildResourceActionMarkup(),
    '</div>'
  ].join("");
}

function buildSelectedResourcePropertyMarkup(selectedItem) {
  if (isResourceGroupItem(selectedItem)) {
    return buildResourceGroupPropertyMarkup(selectedItem);
  }

  if (isNetworkSecurityGroupItem(selectedItem)) {
    return buildNetworkSecurityGroupPropertyMarkup(selectedItem);
  }

  if (isRouteTableItem(selectedItem)) {
    return buildRouteTablePropertyMarkup(selectedItem);
  }

  if (isVirtualNetworkItem(selectedItem)) {
    return buildVirtualNetworkPropertyMarkup(selectedItem);
  }

  if (isSubnetItem(selectedItem)) {
    return buildSubnetPropertyMarkup(selectedItem);
  }

  if (isPublicIpItem(selectedItem)) {
    return buildPublicIpPropertyMarkup(selectedItem);
  }

  if (isDnsZoneItem(selectedItem)) {
    return buildDnsZonePropertyMarkup(selectedItem);
  }

  return buildGenericResourcePropertyMarkup(selectedItem);
}

function getPropertyPanelStateKey() {
  if (state.selectedConnectionId) {
    return `connection:${state.selectedConnectionId}`;
  }

  if (state.selectedResource) {
    return `resource:${state.selectedResource}`;
  }

  return "";
}

function capturePropertyPanelExpansionState() {
  if (!propertyContentEl) {
    return null;
  }

  return {
    groups: Array.from(propertyContentEl.querySelectorAll(".property-group")).map((sectionEl) => Boolean(sectionEl.open)),
    subsections: Array.from(propertyContentEl.querySelectorAll(".property-subsection")).map((sectionEl) => Boolean(sectionEl.open))
  };
}

function applyPropertyPanelExpansionState(expansionState) {
  if (!propertyContentEl || !expansionState || typeof expansionState !== "object") {
    return;
  }

  const groupStates = Array.isArray(expansionState.groups) ? expansionState.groups : [];
  const subsectionStates = Array.isArray(expansionState.subsections) ? expansionState.subsections : [];

  propertyContentEl.querySelectorAll(".property-group").forEach((sectionEl, index) => {
    if (index < groupStates.length) {
      sectionEl.open = Boolean(groupStates[index]);
    }
  });

  propertyContentEl.querySelectorAll(".property-subsection").forEach((sectionEl, index) => {
    if (index < subsectionStates.length) {
      sectionEl.open = Boolean(subsectionStates[index]);
    }
  });
}

function escapeSelectorAttributeValue(value) {
  return String(value ?? "")
    .replace(/\\/g, "\\\\")
    .replace(/"/g, "\\\"");
}

function buildPropertyPanelFocusSelector(element) {
  if (!(element instanceof HTMLElement)) {
    return "";
  }

  const selectorSegments = [element.tagName.toLowerCase()];
  const identityAttributes = [
    "data-resource-field",
    "data-connection-field",
    "data-resource-tag-field",
    "data-string-list-field",
    "data-object-list-field",
    "data-subnet-list-field"
  ];
  const contextAttributes = [
    "data-list-index",
    "data-tag-index",
    "data-object-key",
    "data-list-field"
  ];

  identityAttributes.forEach((attributeName) => {
    const attributeValue = element.getAttribute(attributeName);
    if (attributeValue != null) {
      selectorSegments.push(`[${attributeName}="${escapeSelectorAttributeValue(attributeValue)}"]`);
    }
  });

  contextAttributes.forEach((attributeName) => {
    const attributeValue = element.getAttribute(attributeName);
    if (attributeValue != null) {
      selectorSegments.push(`[${attributeName}="${escapeSelectorAttributeValue(attributeValue)}"]`);
    }
  });

  return selectorSegments.join("");
}

function capturePropertyPanelFocusState() {
  if (!propertyContentEl) {
    return null;
  }

  const activeElement = document.activeElement;
  if (!(activeElement instanceof HTMLElement) || !propertyContentEl.contains(activeElement)) {
    return null;
  }

  const selector = buildPropertyPanelFocusSelector(activeElement);
  if (!selector) {
    return null;
  }

  const focusState = { selector };
  if (typeof activeElement.selectionStart === "number" && typeof activeElement.selectionEnd === "number") {
    focusState.selectionStart = activeElement.selectionStart;
    focusState.selectionEnd = activeElement.selectionEnd;
  }

  return focusState;
}

function restorePropertyPanelFocusState(focusState) {
  if (!propertyContentEl || !focusState || typeof focusState.selector !== "string" || !focusState.selector) {
    return;
  }

  const focusTarget = propertyContentEl.querySelector(focusState.selector);
  if (!(focusTarget instanceof HTMLElement)) {
    return;
  }

  focusTarget.focus({ preventScroll: true });

  if (
    typeof focusState.selectionStart === "number"
    && typeof focusState.selectionEnd === "number"
    && typeof focusTarget.setSelectionRange === "function"
  ) {
    try {
      focusTarget.setSelectionRange(focusState.selectionStart, focusState.selectionEnd);
    } catch {
      return;
    }
  }
}

function updatePropertyPanelForSelection() {
  const previousStateKey = propertyPanelRenderedStateKey;
  if (previousStateKey) {
    const currentExpansionState = capturePropertyPanelExpansionState();
    if (currentExpansionState) {
      propertyPanelExpansionStateByKey.set(previousStateKey, currentExpansionState);
    }
  }

  const nextStateKey = getPropertyPanelStateKey();
  const focusState = previousStateKey && previousStateKey === nextStateKey
    ? capturePropertyPanelFocusState()
    : null;

  const selectedConnection = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId) || null;
  if (selectedConnection) {
    const fromItem = getItemById(selectedConnection.fromId);
    const toItem = getItemById(selectedConnection.toId);
    setSelectedResourceName(selectedConnection.name || "Unnamed Connection");
    
    const connectableItems = state.canvasItems.filter((item) => isConnectableItem(item));
    const fromOptions = connectableItems
      .map((item) => `<option value="${item.id}" ${item.id === selectedConnection.fromId ? "selected" : ""}>${item.name}</option>`)
      .join("");
    const toOptions = connectableItems
      .map((item) => `<option value="${item.id}" ${item.id === selectedConnection.toId ? "selected" : ""}>${item.name}</option>`)
      .join("");
    
    propertyContentEl.innerHTML = [
      "<div class=\"property-form\">",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">Name</span>",
      `<input class=\"property-input\" type=\"text\" value=\"${(selectedConnection.name || "").replace(/\"/g, "&quot;")}\" data-connection-field=\"name\" maxlength=\"80\" />`,
      "</label>",
      "<div class=\"property-row\"><span class=\"property-label\">Type</span><span class=\"property-value\">Connection</span></div>",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">From</span>",
      `<select class=\"property-input\" data-connection-field=\"fromId\"><option value=\"\">Select source...</option>${fromOptions}</select>`,
      "</label>",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">To</span>",
      `<select class=\"property-input\" data-connection-field=\"toId\"><option value=\"\">Select target...</option>${toOptions}</select>`,
      "</label>",
      "<label class=\"property-row\">",
      "<span class=\"property-label\">Direction</span>",
      `<select class=\"property-input\" data-connection-field=\"direction\"><option value=\"one-way\" ${selectedConnection.direction === "one-way" ? "selected" : ""}>One-way</option><option value=\"bi\" ${selectedConnection.direction === "bi" ? "selected" : ""}>Bi-directional</option></select>`,
      "</label>",
      "<div class=\"property-actions\">",
      `<button class=\"btn btn--sm btn--primary\" type=\"button\" data-property-action=\"save\">Save</button>`,
      `<button class=\"btn btn--sm btn--danger\" type=\"button\" data-connection-action=\"remove\">Remove</button>`,
      "</div>"
    ].join("");

    propertyPanelRenderedStateKey = nextStateKey;
    applyPropertyPanelExpansionState(propertyPanelExpansionStateByKey.get(nextStateKey));
    if (focusState) {
      restorePropertyPanelFocusState(focusState);
    }
    return;
  }

  const selectedItem = getItemById(state.selectedResource);
  if (selectedItem) {
    ensureItemProperties(selectedItem);
    setSelectedResourceName(selectedItem.name || selectedItem.resourceType || "Resource");
    propertyContentEl.innerHTML = buildSelectedResourcePropertyMarkup(selectedItem);

    propertyPanelRenderedStateKey = nextStateKey;
    applyPropertyPanelExpansionState(propertyPanelExpansionStateByKey.get(nextStateKey));
    if (focusState) {
      restorePropertyPanelFocusState(focusState);
    }
    return;
  }

  propertyPanelRenderedStateKey = "";
  setSelectedResourceName("None selected");
  propertyContentEl.textContent = "Select a resource or connection to edit property details.";
}

function createResourceRow(category, resource, iconRoot) {
  const row = document.createElement("button");
  row.className = "resource-row";
  row.type = "button";
  row.draggable = true;

  const icon = document.createElement("img");
  icon.className = "resource-icon";
  icon.alt = `${resource.name} icon`;
  const iconSrc = encodeURI(`${iconRoot}/${category}/${resource.icon}`);
  icon.src = iconSrc;
  icon.loading = "lazy";

  const name = document.createElement("span");
  name.className = "resource-name";
  name.textContent = resource.name;

  row.appendChild(icon);
  row.appendChild(name);

  row.addEventListener("dragstart", (event) => {
    const payload = {
      name: resource.name,
      resourceType: resource.name,
      iconSrc,
      category
    };
    event.dataTransfer.effectAllowed = "copy";
    event.dataTransfer.setData("application/json", JSON.stringify(payload));
    event.dataTransfer.setData("text/plain", resource.name);
  });

  row.addEventListener("click", () => {
    // Resources in left panel shouldn't show properties - only canvas items should
    // Just highlight the row for visual feedback, but don't update property panel
    updateTimestamp();
  });

  return row;
}

function renderResources() {
  resourceListEl.innerHTML = "";

  if (!state.currentProject) {
    return;
  }

  const cloudCatalog = cloudCatalogs[state.currentProject.cloud];

  if (!cloudCatalog) {
    const empty = document.createElement("div");
    empty.className = "resource-empty";
    empty.textContent = `${state.currentProject.cloud} resource catalog is not configured yet.`;
    resourceListEl.appendChild(empty);
    state.selectedResource = null;
    updatePropertyPanel(null);
    return;
  }

  const categories = Object.keys(cloudCatalog).sort((first, second) => first.localeCompare(second));
  const iconRoot = getCloudIconRoot(state.currentProject.cloud);
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

    const heading = document.createElement("h4");
    heading.className = "resource-group-title";
    heading.innerHTML = `<span>${titleCase(category)}</span>`;
    
    const groupBody = document.createElement("div");
    groupBody.className = "resource-group-body";

    // Add click handler for collapsible functionality
    heading.addEventListener("click", () => {
      const isCollapsed = heading.classList.toggle("collapsed");
      groupBody.classList.toggle("collapsed", isCollapsed);
    });

    filtered.forEach((resource) => {
      groupBody.appendChild(createResourceRow(category, resource, iconRoot));
    });

    resourceListEl.appendChild(heading);
    resourceListEl.appendChild(groupBody);
  });

  if (!hasVisibleRows) {
    const empty = document.createElement("div");
    empty.className = "resource-empty";
    empty.textContent = "No resources match your search.";
    resourceListEl.appendChild(empty);
  }
}

function updateTimestamp() {
  if (state.currentProject) {
    state.currentProject.lastSaved = Date.now();
    saveCurrentProject();
    setSaveStatus(`Last saved: ${formatTimestamp(state.currentProject.lastSaved)} (Autosave: every 30s)`);
  }
}

function persistCanvasLocal() {
  if (!state.currentProject) {
    return;
  }

  state.currentProject.canvasView = {
    x: state.canvasView.x,
    y: state.canvasView.y,
    zoom: state.canvasView.zoom
  };
  state.currentProject.canvasItems = state.canvasItems.map((item) => ({ ...item }));
  state.currentProject.canvasConnections = state.canvasConnections.map((connection) => ({ ...connection }));
  saveCurrentProject();
}

function toWorldPoint(clientX, clientY) {
  const rect = canvasViewportEl.getBoundingClientRect();
  const screenX = clientX - rect.left;
  const screenY = clientY - rect.top;

  return {
    x: (screenX - state.canvasView.x) / state.canvasView.zoom,
    y: (screenY - state.canvasView.y) / state.canvasView.zoom
  };
}

function getItemById(itemId) {
  return state.canvasItems.find((candidate) => candidate.id === itemId) || null;
}

function getChildrenByParentId(parentId) {
  return state.canvasItems.filter((candidate) => candidate.parentId === parentId);
}

function getItemWorldPosition(itemId) {
  const item = getItemById(itemId);
  if (!item) {
    return { x: 0, y: 0 };
  }

  if (!item.parentId) {
    return { x: item.x, y: item.y };
  }

  const parentWorld = getItemWorldPosition(item.parentId);
  return {
    x: parentWorld.x + CANVAS_CONTAINER.padding + item.x,
    y: parentWorld.y + CANVAS_CONTAINER.headerHeight + CANVAS_CONTAINER.padding + item.y
  };
}

function isDescendant(candidateId, ancestorId) {
  let cursor = getItemById(candidateId);
  while (cursor?.parentId) {
    if (cursor.parentId === ancestorId) {
      return true;
    }
    cursor = getItemById(cursor.parentId);
  }
  return false;
}

function getContainerInnerWorldBounds(container) {
  const world = getItemWorldPosition(container.id);
  const width = clamp(Number(container.width) || CANVAS_CONTAINER.defaultWidth, CANVAS_CONTAINER.minWidth, 2400);
  const height = clamp(Number(container.height) || CANVAS_CONTAINER.defaultHeight, CANVAS_CONTAINER.minHeight, 2400);
  const left = world.x + CANVAS_CONTAINER.padding;
  const top = world.y + CANVAS_CONTAINER.headerHeight + CANVAS_CONTAINER.padding;
  const right = world.x + width - CANVAS_CONTAINER.padding;
  const bottom = world.y + height - CANVAS_CONTAINER.padding;

  return { left, top, right, bottom };
}

function getContainerAtWorldPoint(worldX, worldY, ignoreItemId = null) {
  const containers = state.canvasItems
    .filter((item) => item.isContainer && item.id !== ignoreItemId && !isDescendant(item.id, ignoreItemId))
    .map((item) => ({ item, bounds: getContainerInnerWorldBounds(item) }))
    .filter(({ bounds }) => worldX >= bounds.left && worldX <= bounds.right && worldY >= bounds.top && worldY <= bounds.bottom)
    .sort((first, second) => {
      const firstArea = (first.bounds.right - first.bounds.left) * (first.bounds.bottom - first.bounds.top);
      const secondArea = (second.bounds.right - second.bounds.left) * (second.bounds.bottom - second.bounds.top);
      return firstArea - secondArea;
    });

  return containers.length ? containers[0].item : null;
}

function moveItemToParent(item, nextParentId) {
  const world = getItemWorldPosition(item.id);
  item.parentId = nextParentId;

  if (!nextParentId) {
    item.x = world.x;
    item.y = world.y;
    return;
  }

  const parentWorld = getItemWorldPosition(nextParentId);
  item.x = world.x - parentWorld.x - CANVAS_CONTAINER.padding;
  item.y = world.y - parentWorld.y - CANVAS_CONTAINER.headerHeight - CANVAS_CONTAINER.padding;
}

function removeCanvasItem(itemId) {
  const item = getItemById(itemId);
  if (!item) {
    return;
  }

  const removedIsResourceGroup = isResourceGroupItem(item);
  const removedIsDnsZone = isDnsZoneItem(item);
  const removedIsNetworkSecurityGroup = isNetworkSecurityGroupItem(item);
  const removedIsRouteTable = isRouteTableItem(item);
  const removedIsVirtualNetwork = isVirtualNetworkItem(item);
  const removedIsSubnet = isSubnetItem(item);
  const removedIsPublicIp = isPublicIpItem(item);

  state.canvasItems.forEach((candidate) => {
    if (candidate.id === itemId) {
      return;
    }

    const properties = ensureItemProperties(candidate);

    if (removedIsResourceGroup && supportsResourceGroupBinding(candidate) && properties.resourceGroupRef === itemId) {
      switchResourceGroupToCustom(candidate, item.name);
    }

    if (removedIsDnsZone && isDnsZoneItem(candidate) && properties.parentZoneRef === itemId) {
      switchDnsParentZoneToCustom(candidate, item.name);
    }

    if (isSubnetItem(candidate)) {
      if (removedIsVirtualNetwork && properties.virtualNetworkRef === itemId) {
        switchSubnetParentVirtualNetworkToCustom(candidate, item.name);
      }

      if (removedIsNetworkSecurityGroup && properties.networkSecurityGroupRef === itemId) {
        switchDependencyToCustom(
          properties,
          "networkSecurityGroupMode",
          "networkSecurityGroupRef",
          "networkSecurityGroupName",
          item.name
        );
      }

      if (removedIsRouteTable && properties.routeTableRef === itemId) {
        switchDependencyToCustom(properties, "routeTableMode", "routeTableRef", "routeTableName", item.name);
      }

    }

    if (isNetworkSecurityGroupItem(candidate)) {
      if (removedIsSubnet && properties.associatedSubnetRef === itemId) {
        switchDependencyToCustom(properties, "associatedSubnetMode", "associatedSubnetRef", "associatedSubnetName", item.name);
      }
    }

    if (isVirtualNetworkItem(candidate)) {
      if (removedIsPublicIp && properties.bastionPublicIpRef === itemId) {
        switchDependencyToCustom(properties, "bastionPublicIpMode", "bastionPublicIpRef", "bastionPublicIpName", item.name);
      }

      if (removedIsPublicIp && properties.firewallPublicIpRef === itemId) {
        switchDependencyToCustom(properties, "firewallPublicIpMode", "firewallPublicIpRef", "firewallPublicIpName", item.name);
      }
    }
  });

  const directChildren = getChildrenByParentId(itemId);
  directChildren.forEach((child) => {
    const childWorld = getItemWorldPosition(child.id);
    child.parentId = null;
    child.x = childWorld.x;
    child.y = childWorld.y;
  });

  state.canvasItems = state.canvasItems.filter((candidate) => candidate.id !== itemId);
  state.canvasConnections = state.canvasConnections.filter((connection) => connection.fromId !== itemId && connection.toId !== itemId);

  if (state.selectedResource === itemId) {
    state.selectedResource = null;
    updatePropertyPanel(null);
  }

  if (state.edgeDraft.sourceId === itemId) {
    state.edgeDraft.active = false;
    state.edgeDraft.sourceId = null;
  }

  if (state.selectedConnectionId) {
    const exists = state.canvasConnections.some((connection) => connection.id === state.selectedConnectionId);
    if (!exists) {
      state.selectedConnectionId = null;
    }
  }
}

function buildRemoveControl(itemId) {
  const removeButton = document.createElement("button");
  removeButton.type = "button";
  removeButton.className = "canvas-node-remove";
  removeButton.dataset.itemId = itemId;
  removeButton.setAttribute("aria-label", "Remove resource");
  removeButton.title = "Remove";
  removeButton.textContent = "×";

  return removeButton;
}

function buildConnectHandle(itemId, anchor) {
  const handle = document.createElement("button");
  handle.type = "button";
  handle.className = "canvas-connect-handle";
  handle.dataset.itemId = itemId;
  handle.dataset.anchor = anchor;
  handle.setAttribute("aria-label", `Connect from ${anchor}`);
  return handle;
}

function getNodeScreenRect(itemId) {
  if (!canvasViewportEl) {
    return null;
  }

  const nodeEl = canvasLayerEl?.querySelector(`.canvas-node[data-item-id="${itemId}"]`);
  if (!nodeEl) {
    return null;
  }

  const viewportRect = canvasViewportEl.getBoundingClientRect();
  const nodeRect = nodeEl.getBoundingClientRect();

  return {
    left: nodeRect.left - viewportRect.left,
    top: nodeRect.top - viewportRect.top,
    width: nodeRect.width,
    height: nodeRect.height
  };
}

function getAnchorScreenPoint(itemId, anchor = "right") {
  const rect = getNodeScreenRect(itemId);
  if (!rect) {
    return null;
  }

  const cx = rect.left + rect.width / 2;
  const cy = rect.top + rect.height / 2;

  if (anchor === "top") {
    return { x: cx, y: rect.top };
  }
  if (anchor === "bottom") {
    return { x: cx, y: rect.top + rect.height };
  }
  if (anchor === "left") {
    return { x: rect.left, y: cy };
  }
  return { x: rect.left + rect.width, y: cy };
}

function toWorldFromScreenPoint(screenX, screenY) {
  return {
    x: (screenX - state.canvasView.x) / state.canvasView.zoom,
    y: (screenY - state.canvasView.y) / state.canvasView.zoom
  };
}

function getAnchorWorldPoint(itemId, anchor = "right") {
  const screen = getAnchorScreenPoint(itemId, anchor);
  if (!screen) {
    return null;
  }
  return toWorldFromScreenPoint(screen.x, screen.y);
}

function findClosestByClass(element, className) {
  let cursor = element;
  while (cursor && cursor !== document.body) {
    if (cursor.classList && cursor.classList.contains(className)) {
      return cursor;
    }
    cursor = cursor.parentNode;
  }
  return null;
}

function findInEventPath(event, className) {
  if (!event.composedPath) {
    return findClosestByClass(event.target, className);
  }

  const path = event.composedPath();
  return path.find((node) => node?.classList?.contains?.(className)) || null;
}

function getNearestAnchorFromClient(itemId, clientX, clientY) {
  if (!canvasViewportEl) {
    return "left";
  }

  const rect = getNodeScreenRect(itemId);
  if (!rect) {
    return "left";
  }

  const viewportRect = canvasViewportEl.getBoundingClientRect();
  const sx = clientX - viewportRect.left;
  const sy = clientY - viewportRect.top;

  const distances = [
    { anchor: "top", distance: Math.abs(sy - rect.top) },
    { anchor: "right", distance: Math.abs(sx - (rect.left + rect.width)) },
    { anchor: "bottom", distance: Math.abs(sy - (rect.top + rect.height)) },
    { anchor: "left", distance: Math.abs(sx - rect.left) }
  ];

  distances.sort((first, second) => first.distance - second.distance);
  return distances[0].anchor;
}

function getConnectionPath(connection) {
  const fromWorld = getAnchorWorldPoint(connection.fromId, connection.sourceAnchor || "right");
  const toWorld = getAnchorWorldPoint(connection.toId, connection.targetAnchor || "left");
  if (!fromWorld || !toWorld) {
    return null;
  }

  return {
    path: `M ${fromWorld.x} ${fromWorld.y} L ${toWorld.x} ${toWorld.y}`,
    midX: (fromWorld.x + toWorld.x) / 2,
    midY: (fromWorld.y + toWorld.y) / 2
  };
}

function clearEdgeDraft() {
  state.edgeDraft.active = false;
  state.edgeDraft.sourceId = null;
  state.edgeDraft.sourceAnchor = "right";
  state.edgeDraft.targetId = null;
  state.edgeDraft.targetAnchor = "left";
}

function ensureEdgesDefs() {
  if (!canvasEdgesEl) {
    return;
  }

  if (canvasEdgesEl.querySelector("defs")) {
    return;
  }

  const namespace = "http://www.w3.org/2000/svg";
  const defs = document.createElementNS(namespace, "defs");

  const markerEnd = document.createElementNS(namespace, "marker");
  markerEnd.setAttribute("id", "edge-arrow-end");
  markerEnd.setAttribute("viewBox", "0 0 10 10");
  markerEnd.setAttribute("refX", "9");
  markerEnd.setAttribute("refY", "5");
  markerEnd.setAttribute("markerWidth", "10");
  markerEnd.setAttribute("markerHeight", "10");
  markerEnd.setAttribute("markerUnits", "userSpaceOnUse");
  markerEnd.setAttribute("orient", "auto-start-reverse");

  const arrowEndPath = document.createElementNS(namespace, "path");
  arrowEndPath.setAttribute("d", "M 0 0 L 10 5 L 0 10 z");
  arrowEndPath.setAttribute("fill", "#2563eb");
  markerEnd.appendChild(arrowEndPath);

  defs.appendChild(markerEnd);
  canvasEdgesEl.appendChild(defs);
}

function renderCanvasConnections() {
  if (!canvasEdgesEl || !canvasViewportEl) {
    return;
  }

  ensureEdgesDefs();

  const namespace = "http://www.w3.org/2000/svg";
  const viewportRect = canvasViewportEl.getBoundingClientRect();
  canvasEdgesEl.setAttribute("width", `${Math.max(0, viewportRect.width)}`);
  canvasEdgesEl.setAttribute("height", `${Math.max(0, viewportRect.height)}`);

  const defs = canvasEdgesEl.querySelector("defs");
  canvasEdgesEl.innerHTML = "";
  if (defs) {
    canvasEdgesEl.appendChild(defs);
  } else {
    ensureEdgesDefs();
  }

  state.canvasConnections.forEach((connection) => {
    const pathData = getConnectionPath(connection);
    if (!pathData) {
      return;
    }

    const edge = document.createElementNS(namespace, "path");
    edge.classList.add("canvas-edge");
    edge.dataset.connectionId = connection.id;
    edge.setAttribute("d", pathData.path);

    if (state.selectedConnectionId === connection.id) {
      edge.classList.add("is-selected");
    }

    if (connection.direction === "bi") {
      edge.setAttribute("marker-start", "url(#edge-arrow-end)");
      edge.setAttribute("marker-end", "url(#edge-arrow-end)");
    } else {
      edge.removeAttribute("marker-start");
      edge.setAttribute("marker-end", "url(#edge-arrow-end)");
    }

    canvasEdgesEl.appendChild(edge);
  });

  if (state.edgeDraft.active && state.edgeDraft.sourceId) {
    const sourceWorld = getAnchorWorldPoint(state.edgeDraft.sourceId, state.edgeDraft.sourceAnchor);
    if (sourceWorld) {
      const targetWorld = toWorldPoint(state.edgeDraft.endClientX, state.edgeDraft.endClientY);
      const draftPath = document.createElementNS(namespace, "path");
      draftPath.classList.add("canvas-edge");
      draftPath.setAttribute("d", `M ${sourceWorld.x} ${sourceWorld.y} L ${targetWorld.x} ${targetWorld.y}`);
      draftPath.setAttribute("marker-end", "url(#edge-arrow-end)");
      draftPath.setAttribute("stroke-dasharray", "4 4");
      canvasEdgesEl.appendChild(draftPath);
    }
  }
}

function upsertConnection(fromId, toId, direction, sourceAnchor = "right", targetAnchor = "left") {
  const existing = state.canvasConnections.find((connection) => connection.fromId === fromId && connection.toId === toId);
  if (existing) {
    existing.direction = direction;
    existing.sourceAnchor = sourceAnchor;
    existing.targetAnchor = targetAnchor;
    state.selectedConnectionId = existing.id;

    const fromItem = getItemById(fromId);
    const toItem = getItemById(toId);
    if (fromItem) {
      applyAutoBindingsForItemAndDescendants(fromItem);
    }
    if (toItem) {
      applyAutoBindingsForItemAndDescendants(toItem);
    }

    return;
  }

  const newName = getNextResourceDefaultName();

  const newConnection = {
    id: `conn-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: newName,
    fromId,
    toId,
    direction,
    sourceAnchor,
    targetAnchor
  };

  state.canvasConnections.push(newConnection);
  state.selectedConnectionId = newConnection.id;

  const fromItem = getItemById(fromId);
  const toItem = getItemById(toId);
  if (fromItem) {
    applyAutoBindingsForItemAndDescendants(fromItem);
  }
  if (toItem) {
    applyAutoBindingsForItemAndDescendants(toItem);
  }

  updateCanvasStatus();
}

function updateCanvasNodeSelection() {
  if (!canvasLayerEl) {
    return;
  }

  canvasLayerEl.querySelectorAll(".canvas-node").forEach((nodeEl) => {
    const isSelected = nodeEl.dataset.itemId === state.selectedResource;
    const isReceptor = state.edgeDraft.active && nodeEl.dataset.itemId === state.edgeDraft.targetId;
    nodeEl.classList.toggle("is-selected", isSelected);
    nodeEl.classList.toggle("is-receptor-target", Boolean(isReceptor));

    nodeEl.querySelectorAll(".canvas-connect-handle").forEach((handleEl) => {
      const isActiveReceptor = isReceptor && handleEl.dataset.anchor === state.edgeDraft.targetAnchor;
      handleEl.classList.toggle("is-receptor-active", Boolean(isActiveReceptor));
    });
  });
}

function updateCanvasStatus() {
  if (canvasStatusEl) {
    const resourceCount = state.canvasItems.length;
    const connectionCount = state.canvasConnections.length;
    canvasStatusEl.textContent = `${resourceCount} resource${resourceCount !== 1 ? 's' : ''} · ${connectionCount} connection${connectionCount !== 1 ? 's' : ''}`;
  }
}

/**
 * Render all canvas items using TYPE-BASED visual layering with FLAT DOM structure.
 * 
 * CRITICAL DISTINCTION:
 * - FUNCTIONAL HIERARCHY (parentId): Organizational relationships - where things belong logically
 * - VISUAL LAYERING (type-based): Display order - what appears on top visually
 * 
 * THESE ARE COMPLETELY INDEPENDENT:
 * - A VM in RG1 appears above a VNet in RG2 (visual layer > functional hierarchy)
 * - A VNet may belong to RG1 but be used by resources in RG2 (functional vs visual)
 * - All items are FLAT siblings in DOM - no nesting that would break stacking
 * 
 * VISUAL LAYER ORDER (back to front):
 * Layer 0: Management Groups (furthest back)
 * Layer 1: Subscriptions
 * Layer 2: Resource Groups
 * Layer 3: Virtual Networks
 * Layer 4: Subnets
 * Layer 5: All other resources (closest to front)
 * 
 * RENDERING STRATEGY:
 * - ALL items rendered as flat siblings (direct children of canvasLayerEl)
 * - Items positioned using ABSOLUTE world coordinates
 * - Render order (layer 0 → 5) determines visual stacking via DOM order
 * - parentId used only to calculate world coordinates, NOT for DOM nesting
 */
function renderCanvasItems() {
  if (!canvasLayerEl) {
    return;
  }

  canvasLayerEl.innerHTML = "";

  // Render a single node with absolute world coordinates
  function renderNode(item) {
    // Calculate absolute world coordinates for this item
    const worldCoords = getItemWorldPosition(item.id);
    
    const nodeEl = document.createElement("div");
    nodeEl.className = "canvas-node";
    nodeEl.dataset.itemId = item.id;
    // Use absolute world coordinates instead of relative position
    nodeEl.style.transform = `translate(${worldCoords.x}px, ${worldCoords.y}px)`;
    
    const resourceType = item.resourceType || item.name;

    const iconEl = document.createElement("img");
    iconEl.src = item.iconSrc;
    iconEl.alt = `${resourceType} icon`;
    iconEl.draggable = false;

    if (item.isContainer) {
      const titleEl = document.createElement("span");
      titleEl.textContent = `${resourceType}: ${item.name}`;

      // Render as container
      nodeEl.classList.add("canvas-node--container");
      nodeEl.style.width = `${item.width || CANVAS_CONTAINER.defaultWidth}px`;
      nodeEl.style.height = `${item.height || CANVAS_CONTAINER.defaultHeight}px`;

      const headerEl = document.createElement("div");
      headerEl.className = "canvas-container-header";
      headerEl.appendChild(iconEl);
      headerEl.appendChild(titleEl);
      headerEl.appendChild(buildRemoveControl(item.id));

      const bodyEl = document.createElement("div");
      bodyEl.className = "canvas-container-body";

      nodeEl.appendChild(headerEl);
      nodeEl.appendChild(bodyEl);

      const resizeHandle = document.createElement("div");
      resizeHandle.className = "canvas-resize-handle";
      resizeHandle.dataset.itemId = item.id;
      resizeHandle.title = "Resize container";
      nodeEl.appendChild(resizeHandle);

      // Add connection handles if this container is connectable (networks, subnets)
      if (isConnectableItem(item)) {
        ["top", "right", "bottom", "left"].forEach((anchor) => {
          nodeEl.appendChild(buildConnectHandle(item.id, anchor));
        });
      }
    } else {
      // Render as resource
      nodeEl.classList.add("canvas-node--resource");

      const headerEl = document.createElement("div");
      headerEl.className = "canvas-resource-header";

      const typeEl = document.createElement("span");
      typeEl.className = "canvas-resource-type";
      typeEl.textContent = resourceType;

      headerEl.appendChild(iconEl);
      headerEl.appendChild(typeEl);
      headerEl.appendChild(buildRemoveControl(item.id));

      const bodyEl = document.createElement("div");
      bodyEl.className = "canvas-resource-body";
      bodyEl.textContent = item.name;

      nodeEl.appendChild(headerEl);
      nodeEl.appendChild(bodyEl);

      // Add connection handles to resources
      if (isConnectableItem(item)) {
        ["top", "right", "bottom", "left"].forEach((anchor) => {
          nodeEl.appendChild(buildConnectHandle(item.id, anchor));
        });
      }
    }

    // ALL items appended as flat siblings to canvasLayerEl
    canvasLayerEl.appendChild(nodeEl);
  }

  // Group all items by their visual layer
  const itemsByLayer = {};
  state.canvasItems.forEach(item => {
    const layer = getVisualLayer(item);
    if (!itemsByLayer[layer]) {
      itemsByLayer[layer] = [];
    }
    itemsByLayer[layer].push(item);
  });

  // Render items layer by layer (0 = back, 5 = front)
  // DOM order determines stacking - later = on top
  for (let layer = 0; layer <= 5; layer++) {
    const itemsInLayer = itemsByLayer[layer] || [];
    
    // All items rendered as flat siblings with absolute positioning
    itemsInLayer.forEach(item => {
      renderNode(item);
    });
  }

  updateCanvasNodeSelection();
  renderCanvasConnections();
}

function renderCanvasView() {
  if (!canvasLayerEl || !canvasGridEl) {
    return;
  }

  canvasLayerEl.style.transform = `translate(${state.canvasView.x}px, ${state.canvasView.y}px) scale(${state.canvasView.zoom})`;
  if (canvasEdgesEl) {
    canvasEdgesEl.style.transform = `translate(${state.canvasView.x}px, ${state.canvasView.y}px) scale(${state.canvasView.zoom})`;
  }

  const gridStep = CANVAS_ZOOM.grid * state.canvasView.zoom;
  const offsetX = ((state.canvasView.x % gridStep) + gridStep) % gridStep;
  const offsetY = ((state.canvasView.y % gridStep) + gridStep) % gridStep;
  canvasGridEl.style.backgroundSize = `${gridStep}px ${gridStep}px`;
  canvasGridEl.style.backgroundPosition = `${offsetX}px ${offsetY}px`;

  if (canvasZoomLabelEl) {
    canvasZoomLabelEl.textContent = `${Math.round(state.canvasView.zoom * 100)}%`;
  }

  renderCanvasConnections();
  updateCanvasStatus();
}

function updateCanvasStatus() {
  if (!canvasStatusEl) {
    return;
  }
  
  const resourceCount = state.canvasItems.length;
  const connectionCount = state.canvasConnections.length;
  canvasStatusEl.textContent = `${resourceCount} resource${resourceCount !== 1 ? 's' : ''} · ${connectionCount} connection${connectionCount !== 1 ? 's' : ''}`;
}

function selectCanvasItem(itemId) {
  const item = getItemById(itemId);
  state.selectedResource = item ? item.id : null;
  state.selectedConnectionId = null;
  updatePropertyPanelForSelection();
  updateCanvasNodeSelection();
  renderCanvasConnections();
}

function setCanvasZoom(nextZoom, clientX, clientY) {
  if (!canvasViewportEl) {
    return;
  }

  const boundedZoom = clamp(nextZoom, CANVAS_ZOOM.min, CANVAS_ZOOM.max);
  if (boundedZoom === state.canvasView.zoom) {
    return;
  }

  const rect = canvasViewportEl.getBoundingClientRect();
  const anchorX = typeof clientX === "number" ? clientX - rect.left : rect.width / 2;
  const anchorY = typeof clientY === "number" ? clientY - rect.top : rect.height / 2;

  const worldX = (anchorX - state.canvasView.x) / state.canvasView.zoom;
  const worldY = (anchorY - state.canvasView.y) / state.canvasView.zoom;

  state.canvasView.zoom = boundedZoom;
  state.canvasView.x = anchorX - worldX * boundedZoom;
  state.canvasView.y = anchorY - worldY * boundedZoom;

  renderCanvasView();
  persistCanvasLocal();
}

function adjustCanvasZoom(direction, clientX, clientY) {
  const nextZoom = state.canvasView.zoom + direction * CANVAS_ZOOM.step;
  setCanvasZoom(nextZoom, clientX, clientY);
}

function createCanvasItem(resource, worldX, worldY) {
  const parentContainer = getContainerAtWorldPoint(worldX, worldY);

  let snappedX = Math.round(worldX / 10) * 10;
  let snappedY = Math.round(worldY / 10) * 10;
  let parentId = null;

  if (parentContainer) {
    const parentWorld = getItemWorldPosition(parentContainer.id);
    snappedX = Math.round((worldX - parentWorld.x - CANVAS_CONTAINER.padding) / 10) * 10;
    snappedY = Math.round((worldY - parentWorld.y - CANVAS_CONTAINER.headerHeight - CANVAS_CONTAINER.padding) / 10) * 10;
    parentId = parentContainer.id;
  }

  const containerResource = isContainerResource(resource);
  const resourceType = String(resource.resourceType || resource.name || "Resource");
  const defaultName = getNextResourceDefaultName();

  const newItem = {
    id: `item-${Date.now()}-${Math.random().toString(36).slice(2, 8)}`,
    name: defaultName,
    resourceType,
    iconSrc: resource.iconSrc,
    category: resource.category || "",
    isContainer: containerResource,
    parentId,
    width: containerResource ? CANVAS_CONTAINER.defaultWidth : undefined,
    height: containerResource ? CANVAS_CONTAINER.defaultHeight : undefined,
    x: snappedX,
    y: snappedY,
    properties: createDefaultItemProperties(resourceType, parentContainer)
  };

  syncSubnetItemName(newItem);

  state.canvasItems.push(newItem);
  applyAutoBindingsForItemAndDescendants(newItem);
  renderCanvasItems();
  selectCanvasItem(newItem.id);
  persistCanvasLocal();
  updateCanvasStatus();
}

function initializeCanvasInteractions() {
  if (!canvasViewportEl || !canvasLayerEl || !canvasGridEl) {
    return;
  }

  canvasViewportEl.addEventListener("dragover", (event) => {
    event.preventDefault();
    event.dataTransfer.dropEffect = "copy";
  });

  canvasViewportEl.addEventListener("drop", (event) => {
    event.preventDefault();

    const payload = event.dataTransfer.getData("application/json");
    if (!payload) {
      return;
    }

    try {
      const resource = JSON.parse(payload);
      const world = toWorldPoint(event.clientX, event.clientY);
      createCanvasItem(resource, world.x, world.y);
    } catch {
      // Ignore malformed drag payloads.
    }
  });

  canvasViewportEl.addEventListener("wheel", (event) => {
    event.preventDefault();
    const direction = event.deltaY < 0 ? 1 : -1;
    adjustCanvasZoom(direction, event.clientX, event.clientY);
  }, { passive: false });

  canvasViewportEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0 && event.button !== 1) {
      return;
    }

    if (event.target.closest(".canvas-node")) {
      return;
    }

    event.preventDefault();
    state.selectedConnectionId = null;
    state.selectedResource = null;
    updatePropertyPanelForSelection();
    updateCanvasNodeSelection();
    renderCanvasConnections();
    canvasInteraction.isPanning = true;
    canvasViewportEl.classList.add("is-panning");
    canvasInteraction.panOriginX = event.clientX;
    canvasInteraction.panOriginY = event.clientY;
    canvasInteraction.viewOriginX = state.canvasView.x;
    canvasInteraction.viewOriginY = state.canvasView.y;
  });

  canvasLayerEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0) {
      return;
    }

    const handleEl = event.target.closest(".canvas-connect-handle");
    if (!handleEl) {
      return;
    }

    const sourceItem = getItemById(handleEl.dataset.itemId);
    if (!isConnectableItem(sourceItem)) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    state.edgeDraft.active = true;
    state.edgeDraft.sourceId = handleEl.dataset.itemId;
    state.edgeDraft.sourceAnchor = handleEl.dataset.anchor || "right";
    state.edgeDraft.targetId = null;
    state.edgeDraft.targetAnchor = "left";
    state.edgeDraft.endClientX = event.clientX;
    state.edgeDraft.endClientY = event.clientY;
    state.selectedConnectionId = null;
    renderCanvasConnections();
    updateCanvasNodeSelection();
  });

  canvasLayerEl.addEventListener("mousedown", (event) => {
    if (event.button !== 0) {
      return;
    }

    if (state.edgeDraft.active) {
      return;
    }

    if (event.target.closest(".canvas-connect-handle")) {
      return;
    }

    if (event.target.closest(".canvas-node-remove")) {
      return;
    }

    if (event.target.closest(".canvas-resize-handle")) {
      return;
    }

    const nodeEl = event.target.closest(".canvas-node");
    if (!nodeEl) {
      return;
    }

    const item = getItemById(nodeEl.dataset.itemId);
    if (!item) {
      return;
    }

    if (item.isContainer && !event.target.closest(".canvas-container-header")) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    selectCanvasItem(item.id);
    canvasInteraction.draggingItemId = item.id;
    canvasInteraction.dragOriginX = event.clientX;
    canvasInteraction.dragOriginY = event.clientY;
    canvasInteraction.itemOriginX = item.x;
    canvasInteraction.itemOriginY = item.y;
  });

  canvasLayerEl.addEventListener("click", (event) => {
    const nodeEl = event.target.closest(".canvas-node");
    if (!nodeEl) {
      return;
    }

    selectCanvasItem(nodeEl.dataset.itemId);
  });

  canvasEdgesEl?.addEventListener("click", (event) => {
    const edgeEl = findInEventPath(event, "canvas-edge");
    if (!edgeEl || !edgeEl.dataset.connectionId) {
      state.selectedConnectionId = null;
      updatePropertyPanelForSelection();
      renderCanvasConnections();
      return;
    }

    event.preventDefault();
    event.stopPropagation();
    state.selectedConnectionId = edgeEl.dataset.connectionId;
    state.selectedResource = null;
    updatePropertyPanelForSelection();
    updateCanvasNodeSelection();
    renderCanvasConnections();
  });

  canvasEdgesEl?.addEventListener("mousedown", (event) => {
    if (findInEventPath(event, "canvas-edge")) {
      event.stopPropagation();
    }
  });

  window.addEventListener("mousemove", (event) => {
    if (state.edgeDraft.active) {
      state.edgeDraft.endClientX = event.clientX;
      state.edgeDraft.endClientY = event.clientY;

      const pointerTarget = document.elementFromPoint(event.clientX, event.clientY);
      const receptorHandle = pointerTarget?.closest(".canvas-connect-handle");
      const receptorNode = pointerTarget?.closest(".canvas-node");
      const sourceId = state.edgeDraft.sourceId;

      if (receptorHandle) {
        const targetId = receptorHandle.dataset.itemId;
        const targetItem = getItemById(targetId);
        if (targetId && targetId !== sourceId && isConnectableItem(targetItem)) {
          state.edgeDraft.targetId = targetId;
          state.edgeDraft.targetAnchor = receptorHandle.dataset.anchor || "left";
        } else {
          state.edgeDraft.targetId = null;
          state.edgeDraft.targetAnchor = "left";
        }
      } else if (receptorNode) {
        const targetId = receptorNode.dataset.itemId;
        const targetItem = getItemById(targetId);
        if (targetId && targetId !== sourceId && isConnectableItem(targetItem)) {
          state.edgeDraft.targetId = targetId;
          state.edgeDraft.targetAnchor = getNearestAnchorFromClient(targetId, event.clientX, event.clientY);
        } else {
          state.edgeDraft.targetId = null;
          state.edgeDraft.targetAnchor = "left";
        }
      } else {
        state.edgeDraft.targetId = null;
        state.edgeDraft.targetAnchor = "left";
      }

      updateCanvasNodeSelection();
      renderCanvasConnections();
      return;
    }

    if (canvasInteraction.resizingItemId) {
      const item = getItemById(canvasInteraction.resizingItemId);
      if (!item || !item.isContainer) {
        return;
      }

      const dx = (event.clientX - canvasInteraction.resizeOriginX) / state.canvasView.zoom;
      const dy = (event.clientY - canvasInteraction.resizeOriginY) / state.canvasView.zoom;
      item.width = clamp(canvasInteraction.widthOrigin + dx, CANVAS_CONTAINER.minWidth, 2400);
      item.height = clamp(canvasInteraction.heightOrigin + dy, CANVAS_CONTAINER.minHeight, 2400);
      renderCanvasItems();
      return;
    }

    if (canvasInteraction.isPanning) {
      const dx = event.clientX - canvasInteraction.panOriginX;
      const dy = event.clientY - canvasInteraction.panOriginY;
      state.canvasView.x = canvasInteraction.viewOriginX + dx;
      state.canvasView.y = canvasInteraction.viewOriginY + dy;
      renderCanvasView();
      return;
    }

    if (canvasInteraction.draggingItemId) {
      const item = getItemById(canvasInteraction.draggingItemId);
      if (!item) {
        return;
      }

      const dx = (event.clientX - canvasInteraction.dragOriginX) / state.canvasView.zoom;
      const dy = (event.clientY - canvasInteraction.dragOriginY) / state.canvasView.zoom;
      item.x = Math.round((canvasInteraction.itemOriginX + dx) / 10) * 10;
      item.y = Math.round((canvasInteraction.itemOriginY + dy) / 10) * 10;
      renderCanvasItems();
    }
  });

  window.addEventListener("mouseup", () => {
    if (state.edgeDraft.active && state.edgeDraft.sourceId) {
      const sourceId = state.edgeDraft.sourceId;

      if (state.edgeDraft.targetId && state.edgeDraft.targetId !== sourceId) {
        const targetItem = getItemById(state.edgeDraft.targetId);
        if (isConnectableItem(targetItem)) {
          upsertConnection(sourceId, state.edgeDraft.targetId, "one-way", state.edgeDraft.sourceAnchor, state.edgeDraft.targetAnchor);
          persistCanvasLocal();
        }
      }

      clearEdgeDraft();
      updateCanvasNodeSelection();
      renderCanvasConnections();
    }

    if (canvasInteraction.isPanning) {
      canvasInteraction.isPanning = false;
      canvasViewportEl.classList.remove("is-panning");
      persistCanvasLocal();
    }

    if (canvasInteraction.draggingItemId) {
      const item = getItemById(canvasInteraction.draggingItemId);
      if (item) {
        const world = getItemWorldPosition(item.id);
        const bodyX = world.x + 60;
        const bodyY = world.y + 24;
        const targetContainer = getContainerAtWorldPoint(bodyX, bodyY, item.id);
        const nextParentId = targetContainer ? targetContainer.id : null;

        if (item.parentId !== nextParentId) {
          moveItemToParent(item, nextParentId);
          applyAutoBindingsForItemAndDescendants(item);
          renderCanvasItems();
          if (state.selectedResource === item.id) {
            updatePropertyPanelForSelection();
          }
        }
      }

      canvasInteraction.draggingItemId = null;
      persistCanvasLocal();
    }

    if (canvasInteraction.resizingItemId) {
      canvasInteraction.resizingItemId = null;
      persistCanvasLocal();
    }
  });

  canvasLayerEl.addEventListener("mousedown", (event) => {
    if (event.target.closest(".canvas-node-remove")) {
      return;
    }

    const resizeEl = event.target.closest(".canvas-resize-handle");
    if (!resizeEl) {
      return;
    }

    const item = getItemById(resizeEl.dataset.itemId);
    if (!item || !item.isContainer) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    canvasInteraction.resizingItemId = item.id;
    canvasInteraction.resizeOriginX = event.clientX;
    canvasInteraction.resizeOriginY = event.clientY;
    canvasInteraction.widthOrigin = Number(item.width) || CANVAS_CONTAINER.defaultWidth;
    canvasInteraction.heightOrigin = Number(item.height) || CANVAS_CONTAINER.defaultHeight;
  });

  canvasLayerEl.addEventListener("click", (event) => {
    const removeEl = event.target.closest(".canvas-node-remove");
    if (!removeEl) {
      return;
    }

    event.preventDefault();
    event.stopPropagation();

    removeCanvasItem(removeEl.dataset.itemId);
    renderCanvasItems();
    updateCanvasStatus();
    persistCanvasLocal();
  });

  canvasZoomInBtn?.addEventListener("click", () => adjustCanvasZoom(1));
  canvasZoomOutBtn?.addEventListener("click", () => adjustCanvasZoom(-1));
  canvasResetViewBtn?.addEventListener("click", () => {
    state.canvasView = { x: 0, y: 0, zoom: 1 };
    renderCanvasView();
    persistCanvasLocal();
  });

}

function setSaveStatus(message, isError = false) {
  if (!projectSaveStatus) {
    return;
  }

  projectSaveStatus.textContent = message;
  projectSaveStatus.style.color = isError ? "#b91c1c" : "";
}

function setValidateButtonBusy(isBusy) {
  if (!btnValidate) {
    return;
  }

  const busy = Boolean(isBusy);
  btnValidate.disabled = busy;
  btnValidate.setAttribute("aria-busy", busy ? "true" : "false");
  btnValidate.textContent = busy ? "Validating..." : "Validate";
}


function normalizeSaveTrigger(value) {
  const normalized = String(value || "").trim().toLowerCase();
  if (normalized === "autosave" || normalized === "auto") {
    return "autosave";
  }
  if (normalized === "manual" || normalized === "user") {
    return "manual";
  }
  return "unspecified";
}

function buildProjectSnapshot(options = {}) {
  const saveTrigger = normalizeSaveTrigger(options.saveTrigger);
  return {
    project: {
      id: state.currentProject.id,
      name: state.currentProject.name,
      cloud: state.currentProject.cloud,
      applicationType: String(state.currentProject.applicationType || "").trim(),
      applicationDescription: String(state.currentProject.applicationDescription || "").trim(),
      iacLanguage: String(state.currentProject.iacLanguage || "bicep").trim().toLowerCase(),
      iacParameterFormat: String(state.currentProject.iacParameterFormat || "bicepparam").trim().toLowerCase(),
      lastSaved: state.currentProject.lastSaved
    },
    saveTrigger,
    baseStateHash: String(state.currentProject.canvasStateHash || "").trim(),
    canvasState: {
      leftWidth: state.leftWidth,
      rightWidth: state.rightWidth,
      bottomHeight: state.bottomHeight,
      bottomRightWidth: state.bottomRightWidth,
      selectedResource: state.selectedResource,
      searchTerm: state.searchTerm,
      canvasView: {
        x: state.canvasView.x,
        y: state.canvasView.y,
        zoom: state.canvasView.zoom
      },
      canvasItems: state.canvasItems.map((item) => ({ ...item })),
      canvasConnections: state.canvasConnections.map((connection) => ({ ...connection }))
    }
  };
}

function mergeQueuedSaveOptions(existingOptions, incomingOptions) {
  const incomingSilent = Boolean(incomingOptions?.silent);
  const incomingTrigger = normalizeSaveTrigger(incomingOptions?.saveTrigger);
  if (!existingOptions) {
    return {
      silent: incomingSilent,
      saveTrigger: incomingTrigger
    };
  }

  const existingTrigger = normalizeSaveTrigger(existingOptions.saveTrigger);
  const mergedTrigger = (existingTrigger === "manual" || incomingTrigger === "manual")
    ? "manual"
    : (existingTrigger === "autosave" || incomingTrigger === "autosave" ? "autosave" : "unspecified");

  return {
    silent: Boolean(existingOptions.silent && incomingSilent),
    saveTrigger: mergedTrigger
  };
}

async function runProjectSaveRequest(options = {}) {
  const {
    silent = false,
    saveTrigger = "unspecified"
  } = options;
  const snapshot = buildProjectSnapshot({ saveTrigger });

  const response = await fetch("/api/project/save", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify(snapshot)
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail = payload && typeof payload.detail === "string"
      ? payload.detail.trim()
      : "";
    throw new Error(detail || "Unable to write project files");
  }

  const nextStateHash = payload && typeof payload.stateHash === "string"
    ? payload.stateHash.trim()
    : "";
  if (nextStateHash && state.currentProject) {
    state.currentProject.canvasStateHash = nextStateHash;
    saveCurrentProject();
  }

  if (!silent) {
    setSaveStatus(`Last saved: ${new Date().toLocaleTimeString()} (Autosave: every 30s)`);
  }
}

async function bootstrapFoundryDefaultsOnLoad() {
  try {
    await fetch("/api/foundry/bootstrap-default", {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      }
    });
  } catch {
  }
}

async function saveProjectFiles(options = {}) {
  if (!state.currentProject) {
    return;
  }

  const requestedOptions = {
    silent: Boolean(options.silent),
    saveTrigger: normalizeSaveTrigger(options.saveTrigger)
  };

  if (saveRequestInFlight) {
    queuedSaveOptions = mergeQueuedSaveOptions(queuedSaveOptions, requestedOptions);
    return activeSavePromise;
  }

  saveRequestInFlight = true;
  activeSavePromise = (async () => {
    let nextOptions = requestedOptions;
    while (nextOptions) {
      const runOptions = nextOptions;
      queuedSaveOptions = null;

      try {
        await runProjectSaveRequest(runOptions);
      } catch (error) {
        if (!runOptions.silent) {
          setSaveStatus(error?.message || "Save failed", true);
        }
        throw error;
      }

      nextOptions = queuedSaveOptions;
    }
  })().finally(() => {
    saveRequestInFlight = false;
    activeSavePromise = Promise.resolve();
  });

  return activeSavePromise;
}

// ===== Sizing & Splitters =====
function applySizes() {
  appEl.style.setProperty("--left-width", `${state.leftWidth}%`);
  appEl.style.setProperty("--right-width", `${state.rightWidth}%`);
  appEl.style.setProperty("--bottom-height", `${state.bottomHeight}px`);
  appEl.style.setProperty("--bottom-right-width", `${state.bottomRightWidth}px`);

  if (statusLeftWidthEl) {
    statusLeftWidthEl.textContent = `${Math.round(state.leftWidth)}%`;
  }

  if (statusRightWidthEl) {
    statusRightWidthEl.textContent = `${Math.round(state.rightWidth)}%`;
  }
}

function clamp(value, min, max) {
  return Math.min(Math.max(value, min), max);
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

document.querySelector('[data-splitter="left"]')?.addEventListener("mousedown", (mouseDownEvent) => {
  mouseDownEvent.preventDefault();
  startDrag((event) => {
    const workspaceRect = workspaceEl?.getBoundingClientRect();
    if (!workspaceRect || workspaceRect.width <= 0) {
      return;
    }

    const nextPercent = ((event.clientX - workspaceRect.left) / workspaceRect.width) * 100;
    state.leftWidth = clamp(nextPercent, constraints.leftMin, constraints.leftMax);
  });
});

document.querySelector('[data-splitter="right"]')?.addEventListener("mousedown", (mouseDownEvent) => {
  mouseDownEvent.preventDefault();
  startDrag((event) => {
    const workspaceRect = workspaceEl?.getBoundingClientRect();
    if (!workspaceRect || workspaceRect.width <= 0) {
      return;
    }

    const nextPercent = ((workspaceRect.right - event.clientX) / workspaceRect.width) * 100;
    state.rightWidth = clamp(nextPercent, constraints.rightMin, constraints.rightMax);
  });
});

// ===== Tab Behavior =====
tabGroups.forEach((group) => {
  group.tabs.forEach((tab) => {
    function activateTab() {
      const name = tab.dataset.tab;

      group.tabs.forEach((item) => {
        const active = item === tab;
        item.classList.toggle("is-active", active);
        item.setAttribute("aria-selected", String(active));
        item.setAttribute("tabindex", active ? "0" : "-1");
      });

      group.panels.forEach((panelEl, panelName) => {
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
        const currentIndex = group.tabs.indexOf(tab);
        const direction = event.key === "ArrowRight" ? 1 : -1;
        const nextIndex = (currentIndex + direction + group.tabs.length) % group.tabs.length;
        group.tabs[nextIndex].focus();
      }
    });
  });
});

function setActiveTabByName(tabName) {
  const targetName = String(tabName || "").trim();
  if (!targetName) {
    return;
  }

  tabGroups.forEach((group) => {
    const targetTab = group.tabs.find((item) => item.dataset.tab === targetName);
    if (!targetTab) {
      return;
    }

    group.tabs.forEach((item) => {
      const active = item === targetTab;
      item.classList.toggle("is-active", active);
      item.setAttribute("aria-selected", String(active));
      item.setAttribute("tabindex", active ? "0" : "-1");
    });

    group.panels.forEach((panelEl, panelName) => {
      const hidden = panelName !== targetName;
      panelEl.classList.toggle("is-hidden", hidden);
      panelEl.toggleAttribute("hidden", hidden);
    });
  });
}

function setChatRuntimeValue(targetEl, text, tone = "") {
  if (!targetEl) {
    return;
  }

  targetEl.classList.remove("chat-runtime-value--ok", "chat-runtime-value--warn", "chat-runtime-value--error");
  if (tone === "ok") {
    targetEl.classList.add("chat-runtime-value--ok");
  } else if (tone === "warn") {
    targetEl.classList.add("chat-runtime-value--warn");
  } else if (tone === "error") {
    targetEl.classList.add("chat-runtime-value--error");
  }

  targetEl.textContent = String(text || "");
}

function formatConnectionLabel(connection) {
  if (!connection || typeof connection !== "object") {
    return { text: "Unknown", tone: "warn" };
  }

  if (connection.connected) {
    return { text: "Connected", tone: "ok" };
  }

  if (connection.configured) {
    return { text: "Configured (idle)", tone: "warn" };
  }

  return { text: "Not configured", tone: "error" };
}

function updateChatRuntimeStatus(meta) {
  const runtime = meta && typeof meta === "object" ? meta : {};
  const model = runtime.model && typeof runtime.model === "object" ? runtime.model : {};

  const configuredModel = String(model.configuredModel || "").trim();
  const activeModel = String(model.activeModel || configuredModel || "Rule-based Azure Architect").trim();
  const provider = String(model.provider || "").trim();
  const usedFoundryModel = Boolean(model.usedFoundryModel);

  // Prefer the configured model name for display; fall back to activeModel if unavailable.
  const namedModel = (provider === "azure-foundry" && configuredModel) ? configuredModel : activeModel;
  let modelLabel = namedModel;
  if (provider === "azure-foundry" && configuredModel && !usedFoundryModel) {
    modelLabel = `${namedModel} (fallback active)`;
  }
  if (provider === "azure-foundry" && configuredModel && usedFoundryModel) {
    modelLabel = `${activeModel} (Azure AI Foundry)`;
  }

  const modelTone = usedFoundryModel ? "ok" : (provider === "azure-foundry" ? "warn" : "");
  setChatRuntimeValue(chatRuntimeModelEl, modelLabel, modelTone);

  const connections = runtime.connections && typeof runtime.connections === "object"
    ? runtime.connections
    : {};
  const mcpStatus = formatConnectionLabel(connections.azureMcp);

  setChatRuntimeValue(chatRuntimeMcpEl, mcpStatus.text, mcpStatus.tone);

  const ctxLabel = formatContextWindow(configuredModel || activeModel);
  if (ctxLabel) {
    setChatRuntimeValue(chatRuntimeCtxEl, ctxLabel, "");
  } else {
    setChatRuntimeValue(chatRuntimeCtxEl, "—", "");
  }
}

async function loadArchitectureChatStatus() {
  try {
    const projectId = state.currentProject?.id ? String(state.currentProject.id) : "";
    const query = projectId ? `?projectId=${encodeURIComponent(projectId)}` : "";
    const response = await fetch(`/api/chat/architecture/status${query}`, {
      method: "GET",
      headers: {
        "Accept": "application/json"
      }
    });

    if (!response.ok) {
      throw new Error("Unable to load AI chat runtime status.");
    }

    const payload = await response.json();
    updateChatRuntimeStatus(payload);
  } catch {
    setChatRuntimeValue(chatRuntimeModelEl, "Unavailable", "warn");
    setChatRuntimeValue(chatRuntimeMcpEl, "Unknown", "warn");
    setChatRuntimeValue(chatRuntimeCtxEl, "—", "");
  }
}

const VALIDATION_SEVERITY_ORDER = ["failure", "warning", "info"];
const VALIDATION_SEVERITY_LABELS = {
  failure: "Failures",
  warning: "Warnings",
  info: "Info"
};

function normalizeValidationSeverity(value) {
  const severity = String(value || "").trim().toLowerCase();
  if (severity === "failure" || severity === "error" || severity === "critical" || severity === "high") {
    return "failure";
  }
  if (severity === "warning" || severity === "warn" || severity === "medium") {
    return "warning";
  }
  return "info";
}

function normalizeValidationGroups(result) {
  const grouped = {
    failure: [],
    warning: [],
    info: []
  };

  const addFinding = (finding) => {
    if (!finding || typeof finding !== "object") {
      return;
    }
    const severity = normalizeValidationSeverity(finding.severity);
    grouped[severity].push(finding);
  };

  const rawGroups = result && typeof result === "object" && result.groups && typeof result.groups === "object"
    ? result.groups
    : null;

  if (rawGroups) {
    VALIDATION_SEVERITY_ORDER.forEach((severity) => {
      const entries = Array.isArray(rawGroups[severity]) ? rawGroups[severity] : [];
      entries.forEach(addFinding);
    });
  }

  if (grouped.failure.length || grouped.warning.length || grouped.info.length) {
    return grouped;
  }

  const findings = Array.isArray(result?.findings) ? result.findings : [];
  findings.forEach(addFinding);
  return grouped;
}

function resolvePreferredValidationSeverity(groups) {
  if (validationExpandedSeverity && VALIDATION_SEVERITY_ORDER.includes(validationExpandedSeverity)) {
    return validationExpandedSeverity;
  }

  for (const severity of VALIDATION_SEVERITY_ORDER) {
    if (Array.isArray(groups?.[severity]) && groups[severity].length) {
      return severity;
    }
  }

  return "failure";
}

function summarizeValidationCounts(groups) {
  const failure = Array.isArray(groups?.failure) ? groups.failure.length : 0;
  const warning = Array.isArray(groups?.warning) ? groups.warning.length : 0;
  const info = Array.isArray(groups?.info) ? groups.info.length : 0;
  return {
    failure,
    warning,
    info,
    total: failure + warning + info
  };
}

function normalizeValidationSummary(result, groups) {
  const fallback = summarizeValidationCounts(groups);
  const summary = result && typeof result === "object" && result.summary && typeof result.summary === "object"
    ? result.summary
    : {};

  const parseCount = (value, fallbackValue) => {
    const numericValue = Number(value);
    return Number.isFinite(numericValue) && numericValue >= 0
      ? Math.floor(numericValue)
      : fallbackValue;
  };

  const failure = parseCount(summary.failure, fallback.failure);
  const warning = parseCount(summary.warning, fallback.warning);
  const info = parseCount(summary.info, fallback.info);
  const total = parseCount(summary.total, failure + warning + info);

  return { failure, warning, info, total };
}

function getValidationConnectionLabel(connection, sourceState = "") {
  if (connection && typeof connection === "object") {
    return formatConnectionLabel(connection);
  }

  const stateText = String(sourceState || "").trim().toLowerCase();
  if (stateText === "connected") {
    return { text: "Connected", tone: "ok" };
  }
  if (stateText === "failed" || stateText === "unavailable") {
    return { text: "Failed", tone: "error" };
  }
  if (stateText === "skipped") {
    return { text: "Skipped", tone: "warn" };
  }
  return { text: "Unknown", tone: "warn" };
}

function resolveValidationTargetLabel(target) {
  if (!target || typeof target !== "object") {
    return "";
  }

  const parts = [];

  if (target.resourceId) {
    const resource = getItemById(String(target.resourceId));
    const resourceLabel = resource?.name || String(target.resourceId);
    parts.push(`Resource: ${resourceLabel}`);
  }

  if (target.connectionId) {
    const connectionId = String(target.connectionId);
    const connection = state.canvasConnections.find((item) => item.id === connectionId);
    const connectionLabel = connection?.name || connectionId;
    parts.push(`Connection: ${connectionLabel}`);
  }

  if (target.field) {
    parts.push(`Field: ${String(target.field)}`);
  }

  return parts.join(" · ");
}

function findValidationFindingById(findingId) {
  const safeFindingId = String(findingId || "").trim();
  if (!safeFindingId || !validationResultState) {
    return null;
  }

  const findings = Array.isArray(validationResultState.findings) ? validationResultState.findings : [];
  return findings.find((finding) => String(finding?.id || "").trim() === safeFindingId) || null;
}

function buildCurrentCanvasStatePayload() {
  return {
    leftWidth: state.leftWidth,
    rightWidth: state.rightWidth,
    bottomHeight: state.bottomHeight,
    bottomRightWidth: state.bottomRightWidth,
    selectedResource: state.selectedResource,
    searchTerm: state.searchTerm,
    canvasView: {
      x: state.canvasView.x,
      y: state.canvasView.y,
      zoom: state.canvasView.zoom,
    },
    canvasItems: state.canvasItems.map((item) => ({ ...item })),
    canvasConnections: state.canvasConnections.map((connection) => ({ ...connection }))
  };
}

function applyObjectPathValue(target, pathSegments, value) {
  if (!target || typeof target !== "object" || !Array.isArray(pathSegments) || !pathSegments.length) {
    return false;
  }

  let cursor = target;
  for (let index = 0; index < pathSegments.length - 1; index += 1) {
    const segment = String(pathSegments[index] || "").trim();
    if (!segment) {
      return false;
    }

    const nextValue = cursor[segment];
    if (!nextValue || typeof nextValue !== "object" || Array.isArray(nextValue)) {
      cursor[segment] = {};
    }
    cursor = cursor[segment];
  }

  const lastSegment = String(pathSegments[pathSegments.length - 1] || "").trim();
  if (!lastSegment) {
    return false;
  }

  cursor[lastSegment] = value;
  return true;
}

function applyValidationFixOperations(operations) {
  const attempted = Array.isArray(operations)
    ? operations
      .filter((operation) => operation && typeof operation === "object")
      .map((operation) => ({ ...operation }))
    : [];

  const applied = [];
  const failed = [];
  let changed = false;

  attempted.forEach((operation) => {
    const op = String(operation.op || "").trim().toLowerCase();

    if (!op) {
      failed.push({ operation, reason: "Missing operation type." });
      return;
    }

    try {
      if (op === "set_resource_name") {
        const resourceId = String(operation.resourceId || "").trim();
        const value = String(operation.value || "").trim();
        const resource = getItemById(resourceId);
        if (!resource || !value) {
          failed.push({ operation, reason: "Resource or value not found." });
          return;
        }

        if (resource.name !== value) {
          resource.name = value;
          changed = true;
        }

        applied.push(operation);
        return;
      }

      if (op === "set_resource_property") {
        const resourceId = String(operation.resourceId || "").trim();
        const resource = getItemById(resourceId);
        const rawField = String(operation.field || "").trim();
        if (!resource || !rawField) {
          failed.push({ operation, reason: "Resource or property field not found." });
          return;
        }

        if (rawField === "name") {
          const nextName = String(operation.value || "").trim();
          if (!nextName) {
            failed.push({ operation, reason: "Name value cannot be empty." });
            return;
          }

          if (resource.name !== nextName) {
            resource.name = nextName;
            changed = true;
          }

          applied.push(operation);
          return;
        }

        const properties = ensureItemProperties(resource);
        const normalizedField = rawField.startsWith("properties.")
          ? rawField.slice("properties.".length)
          : rawField;
        const pathSegments = normalizedField
          .split(".")
          .map((segment) => String(segment || "").trim())
          .filter(Boolean);

        if (!pathSegments.length) {
          failed.push({ operation, reason: "Property field is invalid." });
          return;
        }

        const assigned = applyObjectPathValue(properties, pathSegments, operation.value);
        if (!assigned) {
          failed.push({ operation, reason: "Unable to assign property path." });
          return;
        }

        changed = true;
        applied.push(operation);
        return;
      }

      if (op === "remove_connection") {
        const connectionId = String(operation.connectionId || "").trim();
        if (!connectionId) {
          failed.push({ operation, reason: "Connection ID is missing." });
          return;
        }

        const previousLength = state.canvasConnections.length;
        state.canvasConnections = state.canvasConnections.filter((connection) => connection.id !== connectionId);
        if (state.canvasConnections.length === previousLength) {
          failed.push({ operation, reason: "Connection not found." });
          return;
        }

        if (state.selectedConnectionId === connectionId) {
          state.selectedConnectionId = null;
        }

        changed = true;
        applied.push(operation);
        return;
      }

      if (op === "set_connection_direction") {
        const connectionId = String(operation.connectionId || "").trim();
        const direction = String(operation.direction || "one-way").trim().toLowerCase() === "bi" ? "bi" : "one-way";
        const connection = state.canvasConnections.find((item) => item.id === connectionId);
        if (!connection) {
          failed.push({ operation, reason: "Connection not found." });
          return;
        }

        if (connection.direction !== direction) {
          connection.direction = direction;
          changed = true;
        }

        applied.push(operation);
        return;
      }

      if (op === "add_connection") {
        const fromId = String(operation.fromId || "").trim();
        const toId = String(operation.toId || "").trim();
        const direction = String(operation.direction || "one-way").trim().toLowerCase() === "bi" ? "bi" : "one-way";
        const fromItem = getItemById(fromId);
        const toItem = getItemById(toId);

        if (!fromItem || !toItem || fromId === toId) {
          failed.push({ operation, reason: "Connection endpoints are invalid." });
          return;
        }

        const existingConnection = state.canvasConnections.find((connection) => connection.fromId === fromId && connection.toId === toId) || null;
        const previousDirection = existingConnection?.direction || "";
        const previousLength = state.canvasConnections.length;
        upsertConnection(fromId, toId, direction, "right", "left");
        if (state.canvasConnections.length !== previousLength || previousDirection !== direction) {
          changed = true;
        }

        applied.push(operation);
        return;
      }

      if (op === "remove_resource") {
        const resourceId = String(operation.resourceId || "").trim();
        const resource = getItemById(resourceId);
        if (!resource) {
          failed.push({ operation, reason: "Resource not found." });
          return;
        }

        removeCanvasItem(resourceId);
        changed = true;
        applied.push(operation);
        return;
      }

      failed.push({ operation, reason: `Unsupported operation '${op}'.` });
    } catch (error) {
      failed.push({ operation, reason: error?.message || "Unexpected operation failure." });
    }
  });

  if (changed) {
    updatePropertyPanelForSelection();
    renderCanvasItems();
    renderCanvasConnections();
    updateCanvasStatus();
    persistCanvasLocal();
  }

  return {
    attempted,
    applied,
    failed,
    changed,
  };
}

async function requestArchitectureValidation(canvasStatePayload) {
  const projectId = String(state.currentProject?.id || "").trim();
  if (!projectId) {
    throw new Error("Unable to validate architecture: missing project ID.");
  }

  const response = await fetch(`/api/project/${encodeURIComponent(projectId)}/architecture/validation/run`, {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      canvasState: canvasStatePayload,
    })
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail = payload?.detail ? String(payload.detail) : "Architecture validation request failed.";
    throw new Error(detail);
  }

  return payload;
}

async function postArchitectureValidationFixAudit(payload) {
  const projectId = String(state.currentProject?.id || "").trim();
  if (!projectId) {
    return;
  }

  try {
    await fetch(`/api/project/${encodeURIComponent(projectId)}/architecture/validation/fix-audit`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json"
      },
      body: JSON.stringify(payload)
    });
  } catch {
    // Best-effort audit logging only.
  }
}

async function applyValidationFixForFinding(findingId) {
  const safeFindingId = String(findingId || "").trim();
  if (!safeFindingId || validationFixInFlightFindingIds.has(safeFindingId) || validationRunInFlight) {
    return;
  }

  const finding = findValidationFindingById(safeFindingId);
  const fix = finding?.fix && typeof finding.fix === "object" ? finding.fix : null;
  const operations = Array.isArray(fix?.operations) ? fix.operations : [];

  if (!finding || !operations.length) {
    return;
  }

  const beforeStateHash = String(state.currentProject?.canvasStateHash || "").trim();
  const validationRunId = String(validationResultState?.runId || "").trim();

  validationFixInFlightFindingIds.add(safeFindingId);
  validationFixStatusByFindingId.delete(safeFindingId);
  renderValidationTipsPanel();

  await postArchitectureValidationFixAudit({
    validationRunId,
    findingId: safeFindingId,
    status: "attempted",
    suggestionTitle: String(finding.title || "").trim(),
    severity: String(finding.severity || "").trim(),
    attemptedOperations: operations,
    beforeStateHash,
    afterStateHash: beforeStateHash,
    resultSummary: "Fix attempt started."
  });

  try {
    const operationResult = applyValidationFixOperations(operations);

    if (operationResult.changed) {
      updateTimestamp();
      await saveProjectFiles({ silent: true, saveTrigger: "manual" });
    }

    const afterStateHash = String(state.currentProject?.canvasStateHash || beforeStateHash).trim();
    const appliedCount = operationResult.applied.length;
    const failedCount = operationResult.failed.length;
    const attemptedCount = operationResult.attempted.length;

    const finalStatus = appliedCount > 0 ? "applied" : "failed";
    validationFixStatusByFindingId.set(safeFindingId, finalStatus);

    const resultSummary = `Applied ${appliedCount}/${attemptedCount} operation${attemptedCount === 1 ? "" : "s"}${failedCount ? ` (${failedCount} failed).` : "."}`;

    await postArchitectureValidationFixAudit({
      validationRunId,
      findingId: safeFindingId,
      status: finalStatus,
      suggestionTitle: String(finding.title || "").trim(),
      severity: String(finding.severity || "").trim(),
      attemptedOperations: operationResult.attempted,
      beforeStateHash,
      afterStateHash,
      resultSummary,
    });

    setSaveStatus(resultSummary, finalStatus !== "applied");
  } catch (error) {
    validationFixStatusByFindingId.set(safeFindingId, "failed");

    await postArchitectureValidationFixAudit({
      validationRunId,
      findingId: safeFindingId,
      status: "failed",
      suggestionTitle: String(finding.title || "").trim(),
      severity: String(finding.severity || "").trim(),
      attemptedOperations: operations,
      beforeStateHash,
      afterStateHash: String(state.currentProject?.canvasStateHash || beforeStateHash).trim(),
      resultSummary: String(error?.message || "Fix application failed."),
    });

    setSaveStatus(error?.message || "Fix application failed.", true);
  } finally {
    validationFixInFlightFindingIds.delete(safeFindingId);
    renderValidationTipsPanel();
  }
}

function renderValidationTipsPanel() {
  if (!tipsContentEl) {
    return;
  }

  const status = validationStatusState && typeof validationStatusState === "object"
    ? validationStatusState
    : {};
  const result = validationResultState && typeof validationResultState === "object"
    ? validationResultState
    : null;

  if (result?.errorMessage) {
    tipsContentEl.innerHTML = `<div class="validation-empty validation-empty--error">${escapeHtml(String(result.errorMessage))}</div>`;
    return;
  }

  if (!result && !status?.connections) {
    if (tipsInitialMarkup) {
      tipsContentEl.innerHTML = tipsInitialMarkup;
    } else {
      tipsContentEl.textContent = "Run Validate to see architecture recommendations.";
    }
    return;
  }

  const groups = normalizeValidationGroups(result || {});
  validationExpandedSeverity = resolvePreferredValidationSeverity(groups);
  const summary = normalizeValidationSummary(result || {}, groups);

  const modelConfig = status?.model && typeof status.model === "object"
    ? status.model
    : {};
  const modelName = String(modelConfig.configuredModel || modelConfig.activeModel || "Rule checks + Azure MCP").trim() || "Rule checks + Azure MCP";

  const mcpConnection = status?.connections && typeof status.connections === "object"
    ? status.connections.azureMcp
    : null;
  const foundryConnection = status?.connections && typeof status.connections === "object"
    ? status.connections.azureFoundry
    : null;
  const mcpSourceState = result?.sources?.azureMcp?.connectionState;
  const foundrySourceState = result?.sources?.reasoningModel?.connectionState;
  const mcpLabel = getValidationConnectionLabel(mcpConnection, mcpSourceState);
  const foundryLabel = getValidationConnectionLabel(foundryConnection, foundrySourceState);

  const sectionsMarkup = VALIDATION_SEVERITY_ORDER.map((severity) => {
    const findings = Array.isArray(groups[severity]) ? groups[severity] : [];
    const expanded = validationExpandedSeverity === severity;
    const findingMarkup = findings.length
      ? findings.map((finding, index) => {
        const findingId = String(finding?.id || `${severity}-${index + 1}`);
        const title = String(finding?.title || "Recommendation");
        const message = String(finding?.message || "No details provided.");
        const targetLabel = resolveValidationTargetLabel(finding?.target);
        const fix = finding?.fix && typeof finding.fix === "object" ? finding.fix : null;
        const hasOperations = Array.isArray(fix?.operations) && fix.operations.length > 0;
        const fixLabel = String(fix?.label || "Apply fix").trim() || "Apply fix";
        const fixState = validationFixStatusByFindingId.get(findingId) || "";
        const fixRunning = validationFixInFlightFindingIds.has(findingId);

        let buttonText = fixLabel;
        if (fixRunning) {
          buttonText = "Applying...";
        } else if (fixState === "applied") {
          buttonText = "Applied";
        }

        const isDisabled = fixRunning || fixState === "applied";
        const helperText = fixState === "failed"
          ? '<div class="validation-finding__hint validation-finding__hint--error">Last fix attempt failed.</div>'
          : (fixState === "applied"
            ? '<div class="validation-finding__hint validation-finding__hint--ok">Fix applied to canvas.</div>'
            : "");

        return [
          '<article class="validation-finding">',
          `<h4 class="validation-finding__title">${escapeHtml(title)}</h4>`,
          `<p class="validation-finding__message">${escapeHtml(message)}</p>`,
          targetLabel ? `<div class="validation-finding__target">${escapeHtml(targetLabel)}</div>` : "",
          hasOperations
            ? `<div class="validation-finding__actions"><button type="button" class="btn btn--sm btn--primary" data-validation-fix="${escapeHtml(findingId)}" ${isDisabled ? "disabled" : ""}>${escapeHtml(buttonText)}</button></div>`
            : "",
          helperText,
          "</article>"
        ].join("");
      }).join("")
      : '<div class="validation-empty">No items in this severity.</div>';

    return [
      `<section class="validation-group validation-group--${severity}">`,
      `<button type="button" class="validation-group__toggle" data-validation-group-toggle="${severity}" aria-expanded="${expanded ? "true" : "false"}">`,
      `<span class="validation-group__title">${VALIDATION_SEVERITY_LABELS[severity]} (${findings.length})</span>`,
      `<span class="validation-group__chevron" aria-hidden="true">${expanded ? "▾" : "▸"}</span>`,
      "</button>",
      `<div class="validation-group__body${expanded ? "" : " is-hidden"}" ${expanded ? "" : "hidden"}>${findingMarkup}</div>`,
      "</section>"
    ].join("");
  }).join("");

  tipsContentEl.innerHTML = [
    '<div class="validation-runtime">',
    `<div class="validation-runtime__row"><span class="validation-runtime__label">Model</span><span class="status-ai-value">${escapeHtml(modelName)}</span></div>`,
    `<div class="validation-runtime__row"><span class="validation-runtime__label">Azure MCP</span><span class="status-ai-value ${mcpLabel.tone === "ok" ? "chat-runtime-value--ok" : (mcpLabel.tone === "error" ? "chat-runtime-value--error" : "chat-runtime-value--warn")}">${escapeHtml(mcpLabel.text)}</span></div>`,
    `<div class="validation-runtime__row"><span class="validation-runtime__label">Validation Agent</span><span class="status-ai-value ${foundryLabel.tone === "ok" ? "chat-runtime-value--ok" : (foundryLabel.tone === "error" ? "chat-runtime-value--error" : "chat-runtime-value--warn")}">${escapeHtml(foundryLabel.text)}</span></div>`,
    "</div>",
    '<div class="validation-summary">',
    `<span class="validation-summary__item">Failure <strong class="chat-runtime-value--error">${summary.failure}</strong></span>`,
    `<span class="validation-summary__item">Warning <strong class="chat-runtime-value--warn">${summary.warning}</strong></span>`,
    `<span class="validation-summary__item">Info <strong class="chat-runtime-value--ok">${summary.info}</strong></span>`,
    "</div>",
    '<div class="validation-groups">',
    sectionsMarkup,
    "</div>"
  ].join("");
}

async function loadArchitectureValidationStatus(options = {}) {
  const silent = Boolean(options.silent);
  try {
    const projectId = state.currentProject?.id ? String(state.currentProject.id) : "";
    const query = projectId ? `?projectId=${encodeURIComponent(projectId)}` : "";
    const response = await fetch(`/api/validation/architecture/status${query}`, {
      method: "GET",
      headers: {
        "Accept": "application/json"
      }
    });

    if (!response.ok) {
      throw new Error("Unable to load architecture validation status.");
    }

    const payload = await response.json();
    validationStatusState = payload && typeof payload === "object" ? payload : null;
  } catch (error) {
    if (!silent) {
      setSaveStatus(error?.message || "Unable to load architecture validation status.", true);
    }
    if (!validationStatusState) {
      validationStatusState = {
        model: {
          configuredModel: "",
          activeModel: "",
        },
        connections: {
          azureMcp: {
            configured: false,
            connected: false,
          },
          azureFoundry: {
            configured: false,
            connected: false,
          }
        }
      };
    }
  }

  renderValidationTipsPanel();
}

async function runArchitectureValidation() {
  if (!state.currentProject || validationRunInFlight) {
    return;
  }

  validationRunInFlight = true;
  validationResultState = null;
  validationFixStatusByFindingId.clear();
  validationFixInFlightFindingIds.clear();
  setValidateButtonBusy(true);

  try {
    setSaveStatus("Saving current architecture before validation...");
    await saveProjectFiles({ silent: true, saveTrigger: "manual" });

    setSaveStatus("Running architecture validation...");
    const canvasStatePayload = buildCurrentCanvasStatePayload();
    const result = await requestArchitectureValidation(canvasStatePayload);
    validationResultState = result && typeof result === "object" ? result : {};

    const groups = normalizeValidationGroups(validationResultState || {});
    validationExpandedSeverity = resolvePreferredValidationSeverity(groups);
    renderValidationTipsPanel();
    setActiveTabByName("tips");

    const summary = normalizeValidationSummary(validationResultState || {}, groups);
    setSaveStatus(`Validation complete: ${summary.failure} failure, ${summary.warning} warning, ${summary.info} info.`);
    await loadArchitectureValidationStatus({ silent: true });
  } catch (error) {
    validationResultState = {
      errorMessage: error?.message || "Architecture validation failed."
    };
    renderValidationTipsPanel();
    setActiveTabByName("tips");
    setSaveStatus(error?.message || "Architecture validation failed.", true);
  } finally {
    validationRunInFlight = false;
    setValidateButtonBusy(false);
  }
}

tipsContentEl?.addEventListener("click", async (event) => {
  const toggleButton = event.target.closest("[data-validation-group-toggle]");
  if (toggleButton) {
    const severity = String(toggleButton.dataset.validationGroupToggle || "").trim().toLowerCase();
    if (VALIDATION_SEVERITY_ORDER.includes(severity)) {
      validationExpandedSeverity = validationExpandedSeverity === severity ? null : severity;
      renderValidationTipsPanel();
    }
    return;
  }

  const fixButton = event.target.closest("[data-validation-fix]");
  if (fixButton) {
    const findingId = String(fixButton.dataset.validationFix || "").trim();
    if (!findingId) {
      return;
    }

    await applyValidationFixForFinding(findingId);
  }
});

function appendChatMessage(message, autoScroll = true) {
  if (!chatHistoryEl) {
    return;
  }

  const messageEl = document.createElement("div");
  messageEl.className = "chat-message chat-message--user";
  messageEl.textContent = message;
  chatHistoryEl.appendChild(messageEl);
  if (autoScroll) {
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  }

  return messageEl;
}

function appendAssistantMessage(message, autoScroll = true) {
  if (!chatHistoryEl) {
    return null;
  }

  const messageEl = document.createElement("div");
  messageEl.className = "chat-message chat-message--assistant";
  messageEl.textContent = message;
  chatHistoryEl.appendChild(messageEl);
  if (autoScroll) {
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  }
  return messageEl;
}

function appendLoadingMessage() {
  if (!chatHistoryEl) {
    return null;
  }

  const messageEl = document.createElement("div");
  messageEl.className = "chat-message chat-message--assistant chat-message--loading";
  messageEl.textContent = "Thinking...";
  chatHistoryEl.appendChild(messageEl);
  chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  return messageEl;
}

async function loadArchitectureChatHistory() {
  if (!chatHistoryEl) {
    return;
  }

  const projectId = state.currentProject?.id ? String(state.currentProject.id).trim() : "";
  if (!projectId) {
    chatHistoryEl.innerHTML = chatInitialMarkup;
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
    return;
  }

  try {
    const response = await fetch(`/api/chat/architecture/history?projectId=${encodeURIComponent(projectId)}`, {
      method: "GET",
      headers: {
        "Accept": "application/json"
      }
    });

    if (!response.ok) {
      throw new Error("Unable to load AI chat history.");
    }

    const payload = await response.json();
    const threadMessages = Array.isArray(payload?.messages) ? payload.messages : [];

    const validMessages = threadMessages
      .map((item) => {
        const role = String(item?.role || "").trim().toLowerCase();
        const content = String(item?.content || "").trim();
        return { role, content };
      })
      .filter((item) => {
        if (!item.content) {
          return false;
        }
        return item.role === "user" || item.role === "assistant";
      });

    if (!validMessages.length) {
      chatHistoryEl.innerHTML = chatInitialMarkup;
      chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
      return;
    }

    chatHistoryEl.innerHTML = "";
    validMessages.forEach((item) => {
      if (item.role === "user") {
        appendChatMessage(item.content, false);
      } else {
        appendAssistantMessage(item.content, false);
      }
    });
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  } catch {
    chatHistoryEl.innerHTML = chatInitialMarkup;
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  }
}

function setChatBusy(isBusy) {
  chatRequestInFlight = Boolean(isBusy);
  if (chatSendBtn) {
    chatSendBtn.disabled = chatRequestInFlight;
  }
  if (chatInputEl) {
    chatInputEl.disabled = chatRequestInFlight;
  }
}

async function resetChatPanel() {
  chatAgentState = null;
  setChatBusy(false);

  setChatRuntimeValue(chatRuntimeModelEl, "Loading...", "warn");
  setChatRuntimeValue(chatRuntimeMcpEl, "Loading...", "warn");

  if (chatHistoryEl) {
    chatHistoryEl.innerHTML = chatInitialMarkup;
    chatHistoryEl.scrollTop = chatHistoryEl.scrollHeight;
  }

  await Promise.allSettled([
    loadArchitectureChatStatus(),
    loadArchitectureChatHistory(),
  ]);
}

async function requestArchitectureChat(message) {
  const response = await fetch("/api/chat/architecture", {
    method: "POST",
    headers: {
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      message,
      projectId: state.currentProject?.id || null,
      agentState: chatAgentState || null,
    })
  });

  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }

  if (!response.ok) {
    const detail = payload?.detail ? String(payload.detail) : "AI chat request failed.";
    throw new Error(detail);
  }

  return payload;
}

async function sendChatMessage() {
  if (!chatInputEl || chatRequestInFlight) {
    return;
  }

  const message = chatInputEl.value.trim();
  if (!message) {
    return;
  }

  appendChatMessage(message);
  chatInputEl.value = "";

  setChatBusy(true);
  const loadingMessageEl = appendLoadingMessage();

  try {
    const payload = await requestArchitectureChat(message);
    if (loadingMessageEl) {
      loadingMessageEl.remove();
    }

    chatAgentState = payload?.agentState && typeof payload.agentState === "object"
      ? payload.agentState
      : null;

    updateChatRuntimeStatus(payload?.meta);

    const assistantMessage = String(payload?.message || "I could not generate a response.").trim();
    appendAssistantMessage(assistantMessage || "I could not generate a response.");
  } catch (error) {
    if (loadingMessageEl) {
      loadingMessageEl.remove();
    }
    appendAssistantMessage(error?.message || "Unable to complete AI chat request.");
  } finally {
    setChatBusy(false);
    chatInputEl.focus();
  }
}

chatSendBtn?.addEventListener("click", sendChatMessage);
chatInputEl?.addEventListener("keydown", (event) => {
  if (event.key === "Enter" && !event.shiftKey) {
    event.preventDefault();
    sendChatMessage();
  }
});

// ===== Project Name Editing =====
projectNameDisplay?.addEventListener("blur", () => {
  if (!state.currentProject) {
    return;
  }

  const rawSuffix = projectNameDisplay.textContent.trim();
  const { prefix, suffix } = splitProjectName(state.currentProject.cloud, rawSuffix);

  if (suffix) {
    state.currentProject.name = `${prefix}${suffix}`;
    projectNameDisplay.textContent = suffix;
    saveCurrentProject();
    updateTimestamp();
  } else {
    renderProjectName();
  }
});

projectNameDisplay?.addEventListener("input", () => {
  if (!state.currentProject || !projectNameDisplay) {
    return;
  }

  const rawSuffix = projectNameDisplay.textContent || "";
  const { suffix } = splitProjectName(state.currentProject.cloud, rawSuffix);
  if (rawSuffix !== suffix) {
    projectNameDisplay.textContent = suffix;
    placeCaretAtEnd(projectNameDisplay);
  }
});

projectNameDisplay?.addEventListener("keydown", (event) => {
  if (event.key === "Enter") {
    event.preventDefault();
    projectNameDisplay.blur();
  }
});

// ===== Event Listeners =====
btnBackProjects.addEventListener("click", async () => {
  try {
    await saveProjectFiles({ saveTrigger: "manual" });
  } catch {
    // Continue navigation even if file save fails.
  }
  window.location.href = "./landing.html";
});

btnProjectSave?.addEventListener("click", async () => {
  updateTimestamp();
  await saveProjectFiles({ saveTrigger: "manual" });
});

btnValidate?.addEventListener("click", async () => {
  await runArchitectureValidation();
});

btnProjectSettings?.addEventListener("click", () => {
  if (!state.currentProject) {
    return;
  }

  const params = new URLSearchParams();
  params.set("projectId", state.currentProject.id);
  window.location.href = `./project-settings.html?${params.toString()}`;
});

btnGenerateCode?.addEventListener("click", async () => {
  if (!state.currentProject) {
    return;
  }

  const projectId = String(state.currentProject.id || "").trim();
  if (!projectId) {
    setSaveStatus("Unable to start IaC generation: missing project ID.", true);
    return;
  }

  const parameterFormat = String(state.currentProject.iacParameterFormat || "bicepparam").trim().toLowerCase() === "json"
    ? "json"
    : "bicepparam";

  const params = new URLSearchParams();
  params.set("projectId", projectId);
  params.set("autostart", "1");
  params.set("parameterFormat", parameterFormat);

  try {
    updateTimestamp();
    await saveProjectFiles({ silent: true, saveTrigger: "manual" });
  } catch {
    // Continue navigation even if file save fails.
  }

  try {
    sessionStorage.setItem(
      `iac-autostart:${projectId}`,
      JSON.stringify({
        parameterFormat,
        createdAt: Date.now(),
      }),
    );
  } catch {
    // Best-effort helper for robust handoff.
  }

  setSaveStatus("Opening IaC generation...");
  window.location.href = `./iac.html?${params.toString()}`;
});

btnExportDiagram?.addEventListener("click", async (event) => {
  const format = event.shiftKey ? "jpeg" : "png";
  try {
    await exportCurrentDiagram(format);
  } catch (error) {
    setSaveStatus(error?.message || "Export failed", true);
  }
});

searchInput?.addEventListener("input", () => {
  state.searchTerm = searchInput.value;
  renderResources();
});

propertyContentEl?.addEventListener("input", (event) => {
  if (!state.selectedResource) {
    return;
  }

  const selectedItem = getItemById(state.selectedResource);
  if (!selectedItem) {
    return;
  }

  const target = event.target;
  const properties = ensureItemProperties(selectedItem);

  if (target.matches("[data-subnet-list-field]")) {
    const fieldName = String(target.dataset.subnetListField || "");
    const index = Number.parseInt(target.dataset.listIndex || "-1", 10);
    if (!fieldName || !Number.isInteger(index) || index < 0) {
      return;
    }

    if (fieldName === "serviceEndpoints") {
      const editableRows = buildEditableStringRows(properties.serviceEndpoints, {
        maxItems: 40,
        maxLength: 120,
        allowCsv: true
      });
      if (!Object.prototype.hasOwnProperty.call(editableRows, index)) {
        return;
      }

      editableRows[index] = String(target.value || "");
      properties.serviceEndpoints = sanitizeStringList(editableRows, {
        maxItems: 40,
        maxLength: 120,
        allowCsv: true
      });
      persistCanvasLocal();
      return;
    }

    if (fieldName === "delegationServices") {
      const editableRows = buildEditableStringRows(extractDelegationServiceNames(properties.delegations), {
        maxItems: 40,
        maxLength: 120,
        allowCsv: true
      });
      if (!Object.prototype.hasOwnProperty.call(editableRows, index)) {
        return;
      }

      editableRows[index] = String(target.value || "");
      properties.delegations = buildDelegationsFromServiceNames(editableRows);
      persistCanvasLocal();
      return;
    }
  }

  if (target.matches("[data-string-list-field]")) {
    const fieldName = String(target.dataset.stringListField || "");
    const index = Number.parseInt(target.dataset.listIndex || "-1", 10);
    if (!fieldName || !Number.isInteger(index) || index < 0) {
      return;
    }

    const values = ensureStringListField(properties, fieldName);
    values[index] = String(target.value || "").trim();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-object-list-field]")) {
    const fieldName = String(target.dataset.objectListField || "");
    const key = String(target.dataset.objectKey || "");
    const index = Number.parseInt(target.dataset.listIndex || "-1", 10);
    if (!fieldName || !key || !Number.isInteger(index) || index < 0) {
      return;
    }

    const list = ensureObjectListField(properties, fieldName);
    if (!list[index] || typeof list[index] !== "object") {
      list[index] = createDefaultObjectListItem(fieldName, index);
    }

    list[index][key] = normalizeObjectListInputValue(fieldName, key, target.value, list[index][key], index);
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='name']")) {
    selectedItem.name = String(target.value || "");
    setSelectedResourceName(selectedItem.name || selectedItem.resourceType || "Resource");
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='location']")) {
    properties.location = String(target.value || "");
    persistCanvasLocal();
    return;
  }

  if (isVirtualNetworkItem(selectedItem) && target.matches("[data-resource-field='addressPrefixes']")) {
    properties.addressPrefixes = sanitizeStringList(target.value, {
      allowCsv: true,
      maxItems: 20,
      maxLength: 64
    });
    persistCanvasLocal();
    return;
  }

  if (isVirtualNetworkItem(selectedItem) && target.matches("[data-resource-field='dnsServers']")) {
    properties.dnsServers = sanitizeStringList(target.value, {
      allowCsv: true,
      maxItems: 20,
      maxLength: 64
    });
    persistCanvasLocal();
    return;
  }

  if (isDnsZoneItem(selectedItem) && target.matches("[data-resource-field='dnsName']")) {
    properties.dnsName = sanitizeDnsNameValue(target.value || "");
    const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
    if (effectiveDnsName) {
      setSelectedResourceName(effectiveDnsName);
    }
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (isDnsZoneItem(selectedItem) && target.matches("[data-resource-field='parentZoneName']")) {
    properties.parentZoneName = sanitizeDnsNameValue(target.value || "");
    const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
    if (effectiveDnsName) {
      setSelectedResourceName(effectiveDnsName);
    }
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (isDnsZoneItem(selectedItem) && target.matches("[data-resource-field='childDomainName']")) {
    properties.childDomainName = sanitizeDnsNameValue(target.value || "");
    const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
    if (effectiveDnsName) {
      setSelectedResourceName(effectiveDnsName);
    }
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='resourceGroupName']")) {
    properties.resourceGroupName = String(target.value || "");
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='subnetName']")) {
    if (!isSubnetItem(selectedItem)) {
      return;
    }

    const subnetPurpose = normalizeSubnetPurpose(properties.subnetPurpose);
    properties.subnetPurpose = subnetPurpose;
    properties.subnetName = isSubnetNameLocked(subnetPurpose)
      ? getSubnetPredefinedName(subnetPurpose)
      : resolveSubnetNameForPurpose(subnetPurpose, target.value);

    syncSubnetItemName(selectedItem);
    setSelectedResourceName(selectedItem.name || selectedItem.resourceType || "Resource");
    renderCanvasItems();

    persistCanvasLocal();
    return;
  }

  if (
    target.matches("[data-resource-field='bastionName']")
    || target.matches("[data-resource-field='bastionPublicIpName']")
    || target.matches("[data-resource-field='firewallName']")
    || target.matches("[data-resource-field='firewallPolicyName']")
    || target.matches("[data-resource-field='firewallPublicIpName']")
    || target.matches("[data-resource-field='virtualNetworkName']")
    || target.matches("[data-resource-field='networkSecurityGroupName']")
    || target.matches("[data-resource-field='routeTableName']")
    || target.matches("[data-resource-field='associatedSubnetName']")
    || target.matches("[data-resource-field='dnsLabel']")
  ) {
    const fieldName = String(target.dataset.resourceField || "");
    if (fieldName) {
      properties[fieldName] = String(target.value || "");
      persistCanvasLocal();
    }
    return;
  }

  if (target.matches("[data-resource-field='idleTimeoutMinutes']")) {
    properties.idleTimeoutMinutes = normalizeIntegerValue(
      target.value,
      normalizeIntegerValue(properties.idleTimeoutMinutes, 4, 4, 30),
      4,
      30
    );
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-tag-field]")) {
    const index = Number.parseInt(target.dataset.tagIndex || "-1", 10);
    const fieldName = target.dataset.resourceTagField;
    if (!Number.isInteger(index) || index < 0 || (fieldName !== "key" && fieldName !== "value")) {
      return;
    }

    const editableRows = buildEditableTagRows(properties.tags);
    if (!editableRows[index]) {
      return;
    }

    editableRows[index][fieldName] = String(target.value || "");
    properties.tags = sanitizeTagList(editableRows);
    persistCanvasLocal();
    return;
  }
});

propertyContentEl?.addEventListener("click", async (event) => {
  // Handle Save button click
  const saveBtn = event.target.closest("[data-property-action='save']");
  if (saveBtn) {
    persistCanvasLocal();
    renderCanvasItems();
    renderCanvasConnections();

    const originalText = saveBtn.textContent;
    saveBtn.disabled = true;

    try {
      updateTimestamp();
      await saveProjectFiles({ saveTrigger: "manual" });
      saveBtn.textContent = "Saved!";
    } catch {
      saveBtn.textContent = "Save failed";
    }

    setTimeout(() => {
      saveBtn.textContent = originalText;
      saveBtn.disabled = false;
    }, 1500);
    return;
  }

  const listActionEl = event.target.closest("[data-list-action]");
  if (listActionEl && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const action = String(listActionEl.dataset.listAction || "");
    const fieldName = String(listActionEl.dataset.listField || "");
    const index = Number.parseInt(listActionEl.dataset.listIndex || "-1", 10);

    if (action === "add-string" && fieldName) {
      const values = ensureStringListField(properties, fieldName);
      values.push("");
    }

    if (action === "remove-string" && fieldName && Number.isInteger(index) && index >= 0) {
      const values = ensureStringListField(properties, fieldName);
      values.splice(index, 1);
    }

    if (action === "add-object" && fieldName) {
      const values = ensureObjectListField(properties, fieldName);
      values.push(createDefaultObjectListItem(fieldName, values.length));
    }

    if (action === "remove-object" && fieldName && Number.isInteger(index) && index >= 0) {
      const values = ensureObjectListField(properties, fieldName);
      values.splice(index, 1);
    }

    if (fieldName === "securityRules") {
      properties.securityRules = sanitizeNetworkSecurityRules(properties.securityRules);
    }

    if (fieldName === "routes") {
      properties.routes = sanitizeRouteDefinitions(properties.routes);
    }

    if (fieldName === "delegations") {
      properties.delegations = sanitizeSubnetDelegations(properties.delegations);
    }

    if (["addressPrefixes", "dnsServers", "serviceEndpoints"].includes(fieldName)) {
      properties[fieldName] = sanitizeStringList(properties[fieldName], {
        allowCsv: true,
        maxItems: 40,
        maxLength: 200
      });
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  // Handle connection action buttons
  const actionEl = event.target.closest("[data-connection-action]");
  if (!actionEl || !state.selectedConnectionId) {
    const resourceActionEl = event.target.closest("[data-resource-action]");
    if (!resourceActionEl || !state.selectedResource) {
      return;
    }

    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    if (resourceActionEl.dataset.resourceAction === "remove") {
      removeCanvasItem(selectedItem.id);
      state.selectedResource = null;
      updatePropertyPanelForSelection();
      renderCanvasItems();
      updateCanvasStatus();
      persistCanvasLocal();
    }
    return;
  }

  const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
  if (!selected) {
    return;
  }

  const action = actionEl.dataset.connectionAction;

  if (action === "reverse") {
    const fromId = selected.fromId;
    const sourceAnchor = selected.sourceAnchor;
    selected.fromId = selected.toId;
    selected.toId = fromId;
    selected.sourceAnchor = selected.targetAnchor;
    selected.targetAnchor = sourceAnchor;
  }

  if (action === "toggle-direction") {
    selected.direction = selected.direction === "bi" ? "one-way" : "bi";
  }

  if (action === "remove") {
    state.canvasConnections = state.canvasConnections.filter((connection) => connection.id !== state.selectedConnectionId);
    state.selectedConnectionId = null;
    updateCanvasStatus();
  }

  updatePropertyPanelForSelection();
  renderCanvasConnections();
  persistCanvasLocal();
});

propertyContentEl?.addEventListener("change", (event) => {
  const target = event.target;

  if (target.matches("[data-subnet-list-field]") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const fieldName = String(target.dataset.subnetListField || "");
    const index = Number.parseInt(target.dataset.listIndex || "-1", 10);
    if (!fieldName || !Number.isInteger(index) || index < 0) {
      return;
    }

    if (fieldName === "serviceEndpoints") {
      const editableRows = buildEditableStringRows(properties.serviceEndpoints, {
        maxItems: 40,
        maxLength: 120,
        allowCsv: true
      });
      if (Object.prototype.hasOwnProperty.call(editableRows, index)) {
        editableRows[index] = String(target.value || "");
      }

      properties.serviceEndpoints = sanitizeStringList(editableRows, {
        maxItems: 40,
        maxLength: 120,
        allowCsv: true
      });
      updatePropertyPanelForSelection();
      persistCanvasLocal();
      return;
    }

    if (fieldName === "delegationServices") {
      const editableRows = buildEditableStringRows(extractDelegationServiceNames(properties.delegations), {
        maxItems: 40,
        maxLength: 120,
        allowCsv: true
      });
      if (Object.prototype.hasOwnProperty.call(editableRows, index)) {
        editableRows[index] = String(target.value || "");
      }

      properties.delegations = buildDelegationsFromServiceNames(editableRows);
      updatePropertyPanelForSelection();
      persistCanvasLocal();
      return;
    }
  }

  if (target.matches("[data-resource-tag-field]") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const index = Number.parseInt(target.dataset.tagIndex || "-1", 10);
    const fieldName = target.dataset.resourceTagField;
    if (!Number.isInteger(index) || index < 0 || (fieldName !== "key" && fieldName !== "value")) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const editableRows = buildEditableTagRows(properties.tags);
    if (editableRows[index]) {
      editableRows[index][fieldName] = String(target.value || "");
    }

    properties.tags = sanitizeTagList(editableRows);
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='addressPrefixes']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isVirtualNetworkItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    properties.addressPrefixes = sanitizeStringList(target.value, {
      allowCsv: true,
      maxItems: 20,
      maxLength: 64
    });
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='dnsServers']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isVirtualNetworkItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    properties.dnsServers = sanitizeStringList(target.value, {
      allowCsv: true,
      maxItems: 20,
      maxLength: 64
    });
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-string-list-field]") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const fieldName = String(target.dataset.stringListField || "");
    if (!fieldName) {
      return;
    }

    properties[fieldName] = sanitizeStringList(properties[fieldName], {
      allowCsv: true,
      maxItems: 40,
      maxLength: 200
    });
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-object-list-field]") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const fieldName = String(target.dataset.objectListField || "");
    if (!fieldName) {
      return;
    }

    if (fieldName === "securityRules") {
      properties.securityRules = sanitizeNetworkSecurityRules(properties.securityRules);
    }

    if (fieldName === "routes") {
      properties.routes = sanitizeRouteDefinitions(properties.routes);
    }

    if (fieldName === "delegations") {
      properties.delegations = sanitizeSubnetDelegations(properties.delegations);
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-connection-field='name']") && state.selectedConnectionId) {
    const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
    if (!selected) {
      return;
    }

    const nextName = String(target.value || "").trim();
    if (!nextName) {
      updatePropertyPanelForSelection();
      return;
    }

    selected.name = nextName;
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if ((target.matches("[data-connection-field='fromId']") || target.matches("[data-connection-field='toId']")) && state.selectedConnectionId) {
    const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
    if (!selected) {
      return;
    }

    const newValue = target.value;
    if (!newValue) {
      return; // Don't allow empty selection
    }

    const fieldName = target.dataset.connectionField;
    const oppositeField = fieldName === "fromId" ? "toId" : "fromId";
    const oppositeValue = selected[oppositeField];

    // Validation: prevent same resource in both From and To
    if (newValue === oppositeValue) {
      updatePropertyPanelForSelection(); // Revert the change by re-rendering
      return;
    }

    // Update the connection field
    selected[fieldName] = newValue;

    // Update corresponding anchor (default to right for From, left for To)
    if (fieldName === "fromId") {
      selected.sourceAnchor = "right";
    } else {
      selected.targetAnchor = "left";
    }

    const fromItem = getItemById(selected.fromId);
    const toItem = getItemById(selected.toId);
    if (fromItem) {
      applyAutoBindingsForItemAndDescendants(fromItem);
    }
    if (toItem) {
      applyAutoBindingsForItemAndDescendants(toItem);
    }

    updatePropertyPanelForSelection();
    renderCanvasConnections();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-connection-field='direction']") && state.selectedConnectionId) {
    const selected = state.canvasConnections.find((connection) => connection.id === state.selectedConnectionId);
    if (!selected) {
      return;
    }

    selected.direction = target.value === "bi" ? "bi" : "one-way";
    updatePropertyPanelForSelection();
    renderCanvasConnections();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='resourceGroupMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !supportsResourceGroupBinding(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const resourceGroups = getCanvasResourceGroups({ excludeId: selectedItem.id });

    if (target.value === "existing" && resourceGroups.length) {
      const nextResourceGroup = resourceGroups.find((item) => item.id === properties.resourceGroupRef) || resourceGroups[0];
      assignExistingResourceGroup(selectedItem, nextResourceGroup);
    } else {
      switchResourceGroupToCustom(selectedItem);
    }

    if (isDnsZoneItem(selectedItem)) {
      const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
      if (effectiveDnsName) {
        setSelectedResourceName(effectiveDnsName);
      }
    }

    updatePropertyPanelForSelection();
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='resourceGroupRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !supportsResourceGroupBinding(selectedItem)) {
      return;
    }

    const nextResourceGroup = getItemById(target.value);
    if (!nextResourceGroup || !isResourceGroupItem(nextResourceGroup)) {
      return;
    }

    assignExistingResourceGroup(selectedItem, nextResourceGroup);
    if (isDnsZoneItem(selectedItem)) {
      const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
      if (effectiveDnsName) {
        setSelectedResourceName(effectiveDnsName);
      }
    }

    updatePropertyPanelForSelection();
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='bastionPublicIpMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isVirtualNetworkItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const publicIps = getCanvasPublicIps({ excludeId: selectedItem.id });
    if (target.value === "existing" && publicIps.length) {
      const nextPublicIp = publicIps.find((item) => item.id === properties.bastionPublicIpRef) || publicIps[0];
      assignExistingDependency(properties, "bastionPublicIpMode", "bastionPublicIpRef", "bastionPublicIpName", nextPublicIp);
    } else {
      switchDependencyToCustom(properties, "bastionPublicIpMode", "bastionPublicIpRef", "bastionPublicIpName", properties.bastionPublicIpName);
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='bastionPublicIpRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isVirtualNetworkItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const nextPublicIp = getCanvasPublicIps({ excludeId: selectedItem.id }).find((item) => item.id === target.value);
    if (!nextPublicIp) {
      return;
    }

    assignExistingDependency(properties, "bastionPublicIpMode", "bastionPublicIpRef", "bastionPublicIpName", nextPublicIp);
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='firewallPublicIpMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isVirtualNetworkItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const publicIps = getCanvasPublicIps({ excludeId: selectedItem.id });
    if (target.value === "existing" && publicIps.length) {
      const nextPublicIp = publicIps.find((item) => item.id === properties.firewallPublicIpRef) || publicIps[0];
      assignExistingDependency(properties, "firewallPublicIpMode", "firewallPublicIpRef", "firewallPublicIpName", nextPublicIp);
    } else {
      switchDependencyToCustom(properties, "firewallPublicIpMode", "firewallPublicIpRef", "firewallPublicIpName", properties.firewallPublicIpName);
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='firewallPublicIpRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isVirtualNetworkItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const nextPublicIp = getCanvasPublicIps({ excludeId: selectedItem.id }).find((item) => item.id === target.value);
    if (!nextPublicIp) {
      return;
    }

    assignExistingDependency(properties, "firewallPublicIpMode", "firewallPublicIpRef", "firewallPublicIpName", nextPublicIp);
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='subnetPurpose']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isSubnetItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const subnetPurpose = normalizeSubnetPurpose(target.value);
    const previousPurpose = normalizeSubnetPurpose(properties.subnetPurpose);
    const previousName = String(properties.subnetName || "");

    properties.subnetPurpose = subnetPurpose;
    if (isSubnetNameLocked(subnetPurpose)) {
      properties.subnetName = getSubnetPredefinedName(subnetPurpose);
    } else {
      const preferredName = isSubnetNameLocked(previousPurpose)
        ? "default"
        : previousName;
      properties.subnetName = resolveSubnetNameForPurpose(subnetPurpose, preferredName);
    }

    syncSubnetItemName(selectedItem);
    setSelectedResourceName(selectedItem.name || selectedItem.resourceType || "Resource");
    renderCanvasItems();
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='virtualNetworkMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isSubnetItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const virtualNetworks = getCanvasVirtualNetworks({ excludeId: selectedItem.id });
    if (target.value === "existing" && virtualNetworks.length) {
      const nextVirtualNetwork = virtualNetworks.find((item) => item.id === properties.virtualNetworkRef) || virtualNetworks[0];
      assignExistingParentVirtualNetwork(selectedItem, nextVirtualNetwork);
    } else {
      switchSubnetParentVirtualNetworkToCustom(selectedItem, properties.virtualNetworkName);
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='virtualNetworkRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isSubnetItem(selectedItem)) {
      return;
    }

    const nextVirtualNetwork = getCanvasVirtualNetworks({ excludeId: selectedItem.id }).find((item) => item.id === target.value);
    if (!nextVirtualNetwork) {
      return;
    }

    assignExistingParentVirtualNetwork(selectedItem, nextVirtualNetwork);
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='networkSecurityGroupMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isSubnetItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const networkSecurityGroups = getCanvasNetworkSecurityGroups({ excludeId: selectedItem.id });
    if (target.value === "existing" && networkSecurityGroups.length) {
      const nextNetworkSecurityGroup = networkSecurityGroups.find((item) => item.id === properties.networkSecurityGroupRef) || networkSecurityGroups[0];
      assignExistingDependency(
        properties,
        "networkSecurityGroupMode",
        "networkSecurityGroupRef",
        "networkSecurityGroupName",
        nextNetworkSecurityGroup
      );
    } else {
      switchDependencyToCustom(
        properties,
        "networkSecurityGroupMode",
        "networkSecurityGroupRef",
        "networkSecurityGroupName",
        properties.networkSecurityGroupName
      );
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='networkSecurityGroupRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isSubnetItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const nextNetworkSecurityGroup = getCanvasNetworkSecurityGroups({ excludeId: selectedItem.id }).find((item) => item.id === target.value);
    if (!nextNetworkSecurityGroup) {
      return;
    }

    assignExistingDependency(
      properties,
      "networkSecurityGroupMode",
      "networkSecurityGroupRef",
      "networkSecurityGroupName",
      nextNetworkSecurityGroup
    );
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='routeTableMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isSubnetItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const routeTables = getCanvasRouteTables({ excludeId: selectedItem.id });
    if (target.value === "existing" && routeTables.length) {
      const nextRouteTable = routeTables.find((item) => item.id === properties.routeTableRef) || routeTables[0];
      assignExistingDependency(properties, "routeTableMode", "routeTableRef", "routeTableName", nextRouteTable);
    } else {
      switchDependencyToCustom(properties, "routeTableMode", "routeTableRef", "routeTableName", properties.routeTableName);
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='routeTableRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isSubnetItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const nextRouteTable = getCanvasRouteTables({ excludeId: selectedItem.id }).find((item) => item.id === target.value);
    if (!nextRouteTable) {
      return;
    }

    assignExistingDependency(properties, "routeTableMode", "routeTableRef", "routeTableName", nextRouteTable);
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='associatedSubnetMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isNetworkSecurityGroupItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const subnets = getCanvasSubnets({ excludeId: selectedItem.id });
    if (target.value === "existing" && subnets.length) {
      const nextSubnet = subnets.find((item) => item.id === properties.associatedSubnetRef) || subnets[0];
      assignExistingDependency(properties, "associatedSubnetMode", "associatedSubnetRef", "associatedSubnetName", nextSubnet);
    } else {
      switchDependencyToCustom(properties, "associatedSubnetMode", "associatedSubnetRef", "associatedSubnetName", properties.associatedSubnetName);
    }

    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='associatedSubnetRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isNetworkSecurityGroupItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const nextSubnet = getCanvasSubnets({ excludeId: selectedItem.id }).find((item) => item.id === target.value);
    if (!nextSubnet) {
      return;
    }

    assignExistingDependency(properties, "associatedSubnetMode", "associatedSubnetRef", "associatedSubnetName", nextSubnet);
    updatePropertyPanelForSelection();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='dnsMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isDnsZoneItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    properties.dnsMode = target.value === "child" ? "child" : "root";

    if (properties.dnsMode === "child" && !properties.parentZoneName && !properties.childDomainName) {
      const splitName = splitDnsName(properties.dnsName || selectedItem.name);
      properties.parentZoneName = splitName.parentZoneName;
      properties.childDomainName = splitName.childDomainName;
    }

    const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
    if (effectiveDnsName) {
      setSelectedResourceName(effectiveDnsName);
    }

    updatePropertyPanelForSelection();
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='parentZoneMode']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isDnsZoneItem(selectedItem)) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const dnsZones = getCanvasDnsZones({ excludeId: selectedItem.id });

    if (target.value === "existing" && dnsZones.length) {
      const nextParentZone = dnsZones.find((item) => item.id === properties.parentZoneRef) || dnsZones[0];
      assignExistingParentDnsZone(selectedItem, nextParentZone);
    } else {
      switchDnsParentZoneToCustom(selectedItem, properties.parentZoneName);
    }

    const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
    if (effectiveDnsName) {
      setSelectedResourceName(effectiveDnsName);
    }

    updatePropertyPanelForSelection();
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field='parentZoneRef']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isDnsZoneItem(selectedItem)) {
      return;
    }

    const nextParentZone = getItemById(target.value);
    if (!nextParentZone || !isDnsZoneItem(nextParentZone) || nextParentZone.id === selectedItem.id) {
      return;
    }

    assignExistingParentDnsZone(selectedItem, nextParentZone);
    const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
    if (effectiveDnsName) {
      setSelectedResourceName(effectiveDnsName);
    }

    updatePropertyPanelForSelection();
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (
    state.selectedResource
    && (
      target.matches("[data-resource-field='dnsName']")
      || target.matches("[data-resource-field='parentZoneName']")
      || target.matches("[data-resource-field='childDomainName']")
    )
  ) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem || !isDnsZoneItem(selectedItem)) {
      return;
    }

    const effectiveDnsName = applyDnsZoneEffectiveName(selectedItem);
    if (effectiveDnsName) {
      setSelectedResourceName(effectiveDnsName);
    }

    updatePropertyPanelForSelection();
    renderCanvasItems();
    persistCanvasLocal();
    return;
  }

  if (target.matches("[data-resource-field]") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const properties = ensureItemProperties(selectedItem);
    const fieldName = String(target.dataset.resourceField || "");
    const normalizedValue = String(target.value || "").toLowerCase();

    if (RESOURCE_BOOLEAN_SELECT_FIELDS.has(fieldName)) {
      properties[fieldName] = normalizeBooleanValue(target.value, false);
      updatePropertyPanelForSelection();
      persistCanvasLocal();
      return;
    }

    if (fieldName === "idleTimeoutMinutes") {
      properties.idleTimeoutMinutes = normalizeIntegerValue(
        target.value,
        normalizeIntegerValue(properties.idleTimeoutMinutes, 4, 4, 30),
        4,
        30
      );
      updatePropertyPanelForSelection();
      persistCanvasLocal();
      return;
    }

    const enumOptions = RESOURCE_ENUM_SELECT_OPTIONS[fieldName];
    if (enumOptions) {
      if (enumOptions.includes(normalizedValue)) {
        properties[fieldName] = normalizedValue;
      }

      if (fieldName === "sku" && properties.sku === "standardv2") {
        properties.tier = "regional";
        properties.routingPreference = "microsoft-network";
      }

      if (fieldName === "tier" && properties.sku === "standardv2") {
        properties.tier = "regional";
      }

      if (fieldName === "routingPreference" && properties.sku === "standardv2") {
        properties.routingPreference = "microsoft-network";
      }

      updatePropertyPanelForSelection();
      persistCanvasLocal();
      return;
    }
  }

  if (target.matches("[data-resource-field='name']") && state.selectedResource) {
    const selectedItem = getItemById(state.selectedResource);
    if (!selectedItem) {
      return;
    }

    const nextName = String(target.value || "").trim();
    if (!nextName) {
      updatePropertyPanelForSelection();
      return;
    }

    selectedItem.name = nextName;

    updatePropertyPanelForSelection();
    renderCanvasItems();
    renderCanvasConnections();
    persistCanvasLocal();
  }
});

// ===== Initialization =====
async function initialize() {
  // Get projectId from URL params
  const params = new URLSearchParams(window.location.search);
  const projectId = params.get("projectId");

  if (!projectId) {
    console.error("No project ID provided");
    window.location.href = "./landing.html";
    return;
  }

  await bootstrapFoundryDefaultsOnLoad();

  // Load this specific project from backend files
  if (!await loadCurrentProject(projectId)) {
    console.error("Project not found");
    window.location.href = "./landing.html";
    return;
  }

  await Promise.allSettled([
    resetChatPanel(),
    loadArchitectureValidationStatus({ silent: true })
  ]);

  const { prefix, suffix } = splitProjectName(state.currentProject.cloud, state.currentProject.name);
  state.currentProject.name = `${prefix}${suffix}`;
  state.leftWidth = clamp(Number(state.currentProject.leftWidth) || layoutConfig.leftDefault, constraints.leftMin, constraints.leftMax);
  state.rightWidth = clamp(Number(state.currentProject.rightWidth) || layoutConfig.rightDefault, constraints.rightMin, constraints.rightMax);
  state.bottomHeight = clamp(Number(state.currentProject.bottomHeight) || layoutConfig.bottomDefault, constraints.bottomMin, constraints.bottomMax);
  state.bottomRightWidth = clamp(Number(state.currentProject.bottomRightWidth) || layoutConfig.bottomRightDefault, constraints.bottomRightMin, constraints.bottomRightMax);
  state.searchTerm = String(state.currentProject.searchTerm || "");
  state.canvasView = {
    x: Number(state.currentProject.canvasView?.x) || 0,
    y: Number(state.currentProject.canvasView?.y) || 0,
    zoom: clamp(Number(state.currentProject.canvasView?.zoom) || 1, CANVAS_ZOOM.min, CANVAS_ZOOM.max)
  };
  state.canvasItems = Array.isArray(state.currentProject.canvasItems)
    ? state.currentProject.canvasItems.map((item) => ({ ...item }))
    : [];
  state.canvasConnections = Array.isArray(state.currentProject.canvasConnections)
    ? state.currentProject.canvasConnections.map((connection) => ({ ...connection }))
    : [];
  const savedSelectedResource = String(state.currentProject.selectedResource || "").trim();
  state.selectedResource = savedSelectedResource && state.canvasItems.some((item) => item.id === savedSelectedResource)
    ? savedSelectedResource
    : null;

  if (searchInput) {
    searchInput.value = state.searchTerm;
  }

  saveCurrentProject();

  // Update UI with project info
  renderProjectName();
  renderIacIcon();
  updateTimestamp();

  // Initialize layout
  applySizes();
  initializeCanvasInteractions();
  renderCanvasItems();
  renderCanvasView();
  updateCanvasStatus();

  // All core canvas data is ready — lift the skeleton loading state.
  // Resource-list skeleton self-clears when renderResources() runs below.
  // Property-content skeleton self-clears when updatePropertyPanel() runs below.
  appEl.classList.remove("is-canvas-loading");
  window.setInterval(async () => {
    updateTimestamp();

    try {
      await saveProjectFiles({ silent: true, saveTrigger: "autosave" });
    } catch {
      // Keep autosave non-blocking.
    }

    try {
      await loadArchitectureValidationStatus({ silent: true });
    } catch {
      // Keep status refresh non-blocking.
    }
  }, AUTOSAVE_INTERVAL_MS);

  // Load catalog and render resources
  loadCatalogForCloud(state.currentProject.cloud).then(() => {
    renderResources();
  }).catch(() => {
    renderResources();
  });

  // Initialize properties panel
  updatePropertyPanel(null);
}

initialize().catch((error) => {
  console.error("Canvas initialization failed", error);
  if (projectNamePrefixDisplay) {
    projectNamePrefixDisplay.textContent = "Project unavailable";
  }
  if (projectNameDisplay) {
    projectNameDisplay.textContent = "";
  }
  if (resourceListEl) {
    resourceListEl.innerHTML = "";
    const errorRow = document.createElement("div");
    errorRow.className = "resource-empty";
    errorRow.textContent = "Unable to load this project. Return to Projects and reopen.";
    resourceListEl.appendChild(errorRow);
  }
});
