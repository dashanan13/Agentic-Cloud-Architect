#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import re
import signal
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import urlopen

from playwright.async_api import Browser, Error as PlaywrightError, Page, Request, async_playwright


DEFAULT_CDP_URL = "http://127.0.0.1:9222"
DEFAULT_TARGET_URL = "portal.azure.com"
DEFAULT_POLL_SECONDS = 1.5
MUTATING_METHODS = {"PUT", "PATCH", "POST", "DELETE"}
SENSITIVE_KEY_PATTERN = re.compile(
    r"(secret|password|token|key|certificate|private|connectionstring|connstr|sas|signature)",
    re.IGNORECASE,
)
SENSITIVE_VALUE_PATTERN = re.compile(
    r"(AccountKey=|SharedAccessSignature=|sig=|client_secret|password=|Bearer\s+[A-Za-z0-9._-]+)",
    re.IGNORECASE,
)
INJECTION_SCRIPT = r'''
(() => {
  if (window.__azurePortalObserverInstalled) {
    return { installed: true, alreadyInstalled: true };
  }
  window.__azurePortalObserverInstalled = true;

  const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
  const limit = (value, max = 160) => {
    const text = normalize(value);
    return text.length > max ? `${text.slice(0, max - 1)}…` : text;
  };
  const isVisible = (element) => {
    if (!(element instanceof Element)) return false;
    const style = window.getComputedStyle(element);
    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') return false;
    if (element.hasAttribute('hidden') || element.getAttribute('aria-hidden') === 'true') return false;
    const rect = element.getBoundingClientRect();
    return rect.width > 0 && rect.height > 0;
  };
  const safeSelector = (value) => {
    try {
      return CSS.escape(value);
    } catch {
      return value;
    }
  };
  const findBladeTitle = () => {
    const selectors = [
      'h1',
      '[role="heading"][aria-level="1"]',
      '[data-automationid="resource-name"]',
      '[data-automation-id="resource-name"]',
      '.fxs-blade-title-titleText',
      '.ext-hubs-artbrowse-resourceblade-title',
      'h2',
      '[role="heading"][aria-level="2"]'
    ];
    for (const selector of selectors) {
      for (const candidate of document.querySelectorAll(selector)) {
        if (!isVisible(candidate)) continue;
        const text = limit(candidate.textContent || candidate.getAttribute('aria-label') || '');
        if (text) return text;
      }
    }
    return limit(document.title || 'Azure Portal');
  };
  const findLabel = (element) => {
    if (!element) return '';
    if (element.labels && element.labels.length) {
      const label = limit(element.labels[0].textContent);
      if (label) return label;
    }
    if (element.id) {
      const associated = document.querySelector(`label[for="${safeSelector(element.id)}"]`);
      if (associated && isVisible(associated)) {
        const label = limit(associated.textContent);
        if (label) return label;
      }
    }
    const wrappingLabel = element.closest('label');
    if (wrappingLabel && isVisible(wrappingLabel)) {
      const label = limit(wrappingLabel.textContent);
      if (label) return label;
    }
    const ariaLabelledBy = normalize(element.getAttribute('aria-labelledby'));
    if (ariaLabelledBy) {
      const chunks = [];
      for (const id of ariaLabelledBy.split(/\s+/)) {
        const labelNode = document.getElementById(id);
        if (labelNode && isVisible(labelNode)) {
          chunks.push(limit(labelNode.textContent));
        }
      }
      const label = limit(chunks.filter(Boolean).join(' '));
      if (label) return label;
    }
    const attributes = ['aria-label', 'placeholder', 'name', 'id', 'data-automationid', 'data-automation-id'];
    for (const attribute of attributes) {
      const value = limit(element.getAttribute(attribute));
      if (value) return value;
    }
    return '';
  };
  const findSection = (element) => {
    let cursor = element;
    while (cursor && cursor !== document.body) {
      const localHeading = cursor.querySelector?.(':scope > h1, :scope > h2, :scope > h3, :scope > h4, :scope > [role="heading"]');
      if (localHeading && isVisible(localHeading)) {
        const text = limit(localHeading.textContent || localHeading.getAttribute('aria-label') || '');
        if (text) return text;
      }
      const ariaLabel = limit(cursor.getAttribute?.('aria-label'));
      if (ariaLabel && ariaLabel !== findLabel(element) && ariaLabel !== document.title) {
        return ariaLabel;
      }
      cursor = cursor.parentElement;
    }
    return '';
  };
  const listOptions = (element) => {
    if (element instanceof HTMLSelectElement) {
      return Array.from(element.options).slice(0, 80).map((option) => ({
        label: limit(option.textContent || option.label || option.value || ''),
        value: limit(option.value || option.textContent || ''),
      }));
    }
    if (element.getAttribute('role') === 'combobox') {
      const ariaControls = normalize(element.getAttribute('aria-controls'));
      if (ariaControls) {
        const popup = document.getElementById(ariaControls);
        if (popup) {
          return Array.from(popup.querySelectorAll('[role="option"], option')).slice(0, 80).map((option) => ({
            label: limit(option.textContent || option.getAttribute('aria-label') || ''),
            value: limit(option.getAttribute('data-value') || option.getAttribute('value') || option.textContent || ''),
          }));
        }
      }
    }
    return [];
  };
  const selectedOption = (element) => {
    if (element instanceof HTMLSelectElement) {
      const selected = element.selectedOptions && element.selectedOptions[0];
      return selected ? limit(selected.textContent || selected.label || selected.value || '') : '';
    }
    const role = normalize(element.getAttribute('role')).toLowerCase();
    if (role === 'combobox') {
      return limit(element.getAttribute('aria-label') || element.textContent || element.value || '');
    }
    if (element instanceof HTMLInputElement && ['radio', 'checkbox'].includes(element.type.toLowerCase())) {
      return limit(element.value || '');
    }
    return '';
  };
  const describeElement = (element) => {
    if (!(element instanceof Element)) return null;
    const tagName = element.tagName.toLowerCase();
    const role = normalize(element.getAttribute('role')).toLowerCase();
    const inputType = tagName === 'input' ? normalize(element.getAttribute('type')).toLowerCase() || 'text' : '';
    const controlType = role || inputType || tagName;
    const label = findLabel(element);
    const section = findSection(element);
    const placeholder = limit(element.getAttribute('placeholder') || '');
    const isNativeDisabled = 'disabled' in element ? Boolean(element.disabled) : false;
    const checked = tagName === 'input' && ['checkbox', 'radio'].includes(inputType)
      ? Boolean(element.checked)
      : (role === 'checkbox' || role === 'switch' || role === 'radio')
        ? element.getAttribute('aria-checked') === 'true'
        : null;
    const valueCapable = tagName === 'input' || tagName === 'textarea' || tagName === 'select';
    const hasValue = valueCapable
      ? normalize(element.value || '').length > 0
      : normalize(element.textContent || '').length > 0;
    const options = listOptions(element);
    const selected = selectedOption(element);
    const key = [section, label, controlType].filter(Boolean).join(' :: ') || `${tagName}::${element.id || element.getAttribute('name') || element.className || 'field'}`;
    return {
      key,
      label,
      section,
      tagName,
      controlType,
      placeholder,
      required: element.getAttribute('aria-required') === 'true' || Boolean(element.required),
      disabled: isNativeDisabled || element.getAttribute('aria-disabled') === 'true',
      checked,
      hasValue,
      selectedOption: selected,
      options,
    };
  };
  const describeTarget = (target) => {
    const element = target instanceof Element ? target.closest('input, textarea, select, [role="combobox"], [role="checkbox"], [role="switch"], [role="radio"], [role="spinbutton"], button[aria-haspopup="listbox"], button') : null;
    if (!element) return { label: '', section: '', controlType: 'unknown' };
    const described = describeElement(element) || { label: '', section: '', controlType: 'unknown' };
    return {
      label: described.label,
      section: described.section,
      controlType: described.controlType,
      selectedOption: described.selectedOption,
      checked: described.checked,
      hasValue: described.hasValue,
      tagName: described.tagName,
    };
  };
  const collectSnapshot = () => {
    const selector = [
      'input',
      'textarea',
      'select',
      '[role="combobox"]',
      '[role="checkbox"]',
      '[role="switch"]',
      '[role="radio"]',
      '[role="spinbutton"]',
      'button[aria-haspopup="listbox"]'
    ].join(', ');
    const seen = new Set();
    const fields = [];
    for (const element of document.querySelectorAll(selector)) {
      if (!isVisible(element)) continue;
      const described = describeElement(element);
      if (!described) continue;
      if (!described.label && !described.selectedOption && !described.placeholder) continue;
      if (seen.has(described.key)) continue;
      seen.add(described.key);
      fields.push(described);
    }
    fields.sort((left, right) => {
      const a = `${left.section} ${left.label} ${left.controlType}`.toLowerCase();
      const b = `${right.section} ${right.label} ${right.controlType}`.toLowerCase();
      return a.localeCompare(b);
    });
    return {
      bladeTitle: findBladeTitle(),
      pageTitle: limit(document.title || 'Azure Portal'),
      fieldCount: fields.length,
      fields,
    };
  };
  const emit = async (type, data) => {
    if (typeof window.__portalObserverEmit !== 'function') return;
    try {
      await window.__portalObserverEmit({
        type,
        data,
        emittedAt: new Date().toISOString(),
        url: window.location.href,
        title: document.title,
      });
    } catch (error) {
      console.debug('azure portal observer emit failed', error);
    }
  };

  let timer = null;
  const scheduleSnapshot = (reason, meta) => {
    if (timer) clearTimeout(timer);
    timer = window.setTimeout(() => {
      emit('snapshot', {
        reason,
        meta,
        snapshot: collectSnapshot(),
      });
    }, 700);
  };

  document.addEventListener('click', (event) => {
    scheduleSnapshot('click', describeTarget(event.target));
  }, true);
  document.addEventListener('change', (event) => {
    scheduleSnapshot('change', describeTarget(event.target));
  }, true);
  window.addEventListener('popstate', () => scheduleSnapshot('popstate', { label: 'history', controlType: 'navigation' }));
  window.addEventListener('hashchange', () => scheduleSnapshot('hashchange', { label: 'hashchange', controlType: 'navigation' }));

  const mutationObserver = new MutationObserver((mutations) => {
    const meaningful = mutations.some((mutation) => mutation.type === 'childList' || mutation.attributeName === 'aria-expanded' || mutation.attributeName === 'aria-hidden');
    if (meaningful) {
      scheduleSnapshot('mutation', { label: 'dom-change', controlType: 'mutation', mutationCount: mutations.length });
    }
  });
  mutationObserver.observe(document.documentElement || document.body, {
    subtree: true,
    childList: true,
    attributes: true,
    attributeFilter: ['aria-expanded', 'aria-hidden', 'aria-checked', 'aria-disabled', 'hidden', 'class'],
  });

  emit('status', {
    message: 'observer-installed',
    snapshot: collectSnapshot(),
  });
  return { installed: true };
})();
'''


@dataclass
class ObserverConfig:
    cdp_url: str
    target_url_substring: str
    output_dir: Path
    poll_seconds: float
    launch_chrome: bool
    chrome_path: str | None
    open_url: str
    chrome_profile_dir: Path


class AzurePortalObserver:
    def __init__(self, config: ObserverConfig) -> None:
        self.config = config
        self.events_path = config.output_dir / "events.ndjson"
        self.requests_path = config.output_dir / "requests.ndjson"
        self.edges_path = config.output_dir / "candidate_edges.ndjson"
        self.latest_snapshot_path = config.output_dir / "latest_snapshot.json"
        self.manifest_path = config.output_dir / "manifest.json"
        self.snapshots_dir = config.output_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.last_snapshot: dict[str, Any] | None = None
        self.snapshot_index = 0
        self._stop_event = asyncio.Event()

    def stop(self) -> None:
        self._stop_event.set()

    async def wait_until_stopped(self) -> None:
        await self._stop_event.wait()

    def initialize_output(self) -> None:
        manifest = {
            "startedAt": utc_now(),
            "cdpUrl": self.config.cdp_url,
            "targetUrlSubstring": self.config.target_url_substring,
            "outputDir": str(self.config.output_dir),
            "eventsFile": str(self.events_path),
            "requestsFile": str(self.requests_path),
            "edgesFile": str(self.edges_path),
            "latestSnapshotFile": str(self.latest_snapshot_path),
        }
        write_json(self.manifest_path, manifest)

    async def wait_for_browser(self, playwright) -> Browser:
        print(f"[observer] Waiting for Chrome DevTools endpoint at {self.config.cdp_url} ...", flush=True)
        while not self._stop_event.is_set():
            if endpoint_is_ready(self.config.cdp_url):
                browser = await playwright.chromium.connect_over_cdp(self.config.cdp_url)
                print("[observer] Connected to Chrome.", flush=True)
                return browser
            await asyncio.sleep(self.config.poll_seconds)
        raise RuntimeError("Observer stopped before Chrome DevTools endpoint became available.")

    async def wait_for_target_page(self, browser: Browser) -> Page:
        target = self.config.target_url_substring.lower()
        print(f"[observer] Waiting for a tab containing '{self.config.target_url_substring}' ...", flush=True)
        while not self._stop_event.is_set():
            for context in browser.contexts:
                for page in context.pages:
                    url = (page.url or "").lower()
                    if target in url:
                        print(f"[observer] Attached to tab: {page.url}", flush=True)
                        return page
            await asyncio.sleep(self.config.poll_seconds)
        raise RuntimeError("Observer stopped before a matching page was found.")

    async def attach(self, page: Page) -> None:
        page.on("request", lambda request: asyncio.create_task(self.record_request(request)))
        page.on("close", lambda _: self.stop())
        try:
            await page.expose_binding("__portalObserverEmit", self.handle_browser_event)
        except PlaywrightError as exc:
            if "already" not in str(exc).lower():
                raise
        await page.add_init_script(INJECTION_SCRIPT)
        for _ in range(6):
            try:
                await page.evaluate(INJECTION_SCRIPT)
                return
            except PlaywrightError as exc:
                message = str(exc).lower()
                if "execution context was destroyed" in message or "cannot find context" in message or "navigating" in message:
                    await asyncio.sleep(1)
                    continue
                raise

    async def handle_browser_event(self, source, payload: Any) -> None:
        if not isinstance(payload, dict):
            return
        event_type = str(payload.get("type") or "unknown")
        data = payload.get("data") if isinstance(payload.get("data"), dict) else {}
        snapshot = data.get("snapshot") if isinstance(data.get("snapshot"), dict) else None
        diff = build_snapshot_diff(self.last_snapshot, snapshot)
        event_record = {
            "timestamp": str(payload.get("emittedAt") or utc_now()),
            "type": event_type,
            "reason": str(data.get("reason") or data.get("message") or event_type),
            "url": str(payload.get("url") or ""),
            "pageTitle": str(payload.get("title") or ""),
            "bladeTitle": str((snapshot or {}).get("bladeTitle") or ""),
            "eventSource": sanitize_event_source(data.get("meta")),
            "summary": summarize_diff(diff),
        }
        append_ndjson(self.events_path, event_record)
        if snapshot:
            write_json(self.latest_snapshot_path, snapshot)
            if should_persist_snapshot(event_type, diff):
                self.snapshot_index += 1
                snapshot_name = f"{self.snapshot_index:04d}-{slugify(event_record['reason']) or 'snapshot'}.json"
                write_json(self.snapshots_dir / snapshot_name, {
                    "metadata": event_record,
                    "snapshot": snapshot,
                    "diff": diff,
                })
            self.emit_candidate_edges(event_record, diff)
            self.last_snapshot = snapshot

    def emit_candidate_edges(self, event_record: dict[str, Any], diff: dict[str, Any]) -> None:
        source = event_record.get("eventSource") or {}
        source_label = str(source.get("label") or "").strip()
        if not source_label:
            return
        source_section = str(source.get("section") or "").strip()
        source_selected = str(source.get("selectedOption") or "").strip()
        base_source = {
            "label": source_label,
            "section": source_section,
            "controlType": str(source.get("controlType") or ""),
            "selectedOption": source_selected,
            "checked": source.get("checked"),
            "hasValue": source.get("hasValue"),
        }
        for relation, key in (("shows", "appeared"), ("hides", "disappeared")):
            for field in diff.get(key, []):
                append_ndjson(self.edges_path, {
                    "timestamp": event_record.get("timestamp"),
                    "relation": relation,
                    "source": base_source,
                    "target": {
                        "key": field.get("key"),
                        "label": field.get("label"),
                        "section": field.get("section"),
                        "controlType": field.get("controlType"),
                    },
                })

    async def record_request(self, request: Request) -> None:
        if not should_record_request(request):
            return
        payload: Any = None
        post_data = request.post_data
        if post_data:
            payload = try_parse_json(post_data)
            if payload is None:
                payload = redact_text(post_data)
            else:
                payload = redact_data(payload)
        record = {
            "timestamp": utc_now(),
            "method": request.method,
            "url": request.url,
            "resourceType": request.resource_type,
            "headers": redact_headers(request.headers),
            "payload": payload,
        }
        append_ndjson(self.requests_path, record)


def should_record_request(request: Request) -> bool:
    url = request.url.lower()
    if "management.azure.com" in url and request.method.upper() in MUTATING_METHODS:
        return True
    if "management.azure.com" in url and "/providers/" in url and "api-version=" in url:
        return True
    if "portal.azure.com" in url and request.method.upper() in MUTATING_METHODS:
        return True
    return False


def sanitize_event_source(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    return {
        "label": str(value.get("label") or "").strip(),
        "section": str(value.get("section") or "").strip(),
        "controlType": str(value.get("controlType") or "").strip(),
        "selectedOption": str(value.get("selectedOption") or "").strip(),
        "checked": value.get("checked"),
        "hasValue": value.get("hasValue"),
        "tagName": str(value.get("tagName") or "").strip(),
    }


def should_persist_snapshot(event_type: str, diff: dict[str, Any]) -> bool:
    if event_type == "status":
        return True
    return bool(diff.get("appeared") or diff.get("disappeared") or diff.get("changed"))


def build_snapshot_diff(previous: dict[str, Any] | None, current: dict[str, Any] | None) -> dict[str, Any]:
    if not current:
        return {"appeared": [], "disappeared": [], "changed": []}
    current_fields = {field.get("key"): field for field in current.get("fields", []) if isinstance(field, dict) and field.get("key")}
    if not previous:
        return {
            "appeared": list(current_fields.values()),
            "disappeared": [],
            "changed": [],
        }
    previous_fields = {field.get("key"): field for field in previous.get("fields", []) if isinstance(field, dict) and field.get("key")}
    appeared_keys = sorted(set(current_fields) - set(previous_fields))
    disappeared_keys = sorted(set(previous_fields) - set(current_fields))
    changed: list[dict[str, Any]] = []
    for key in sorted(set(previous_fields) & set(current_fields)):
        before = previous_fields[key]
        after = current_fields[key]
        delta: dict[str, Any] = {}
        for field_name in ("selectedOption", "checked", "disabled", "required", "hasValue"):
            if before.get(field_name) != after.get(field_name):
                delta[field_name] = {
                    "before": before.get(field_name),
                    "after": after.get(field_name),
                }
        before_options = before.get("options") or []
        after_options = after.get("options") or []
        if before_options != after_options:
            delta["optionsCount"] = {
                "before": len(before_options),
                "after": len(after_options),
            }
        if delta:
            changed.append({
                "key": key,
                "label": after.get("label") or before.get("label"),
                "section": after.get("section") or before.get("section"),
                "controlType": after.get("controlType") or before.get("controlType"),
                "changes": delta,
            })
    return {
        "appeared": [current_fields[key] for key in appeared_keys],
        "disappeared": [previous_fields[key] for key in disappeared_keys],
        "changed": changed,
    }


def summarize_diff(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        "appearedCount": len(diff.get("appeared", [])),
        "disappearedCount": len(diff.get("disappeared", [])),
        "changedCount": len(diff.get("changed", [])),
        "appeared": [field.get("key") for field in diff.get("appeared", [])[:12]],
        "disappeared": [field.get("key") for field in diff.get("disappeared", [])[:12]],
        "changed": [field.get("key") for field in diff.get("changed", [])[:12]],
    }


def append_ndjson(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def slugify(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip().lower())
    cleaned = cleaned.strip("-._")
    return cleaned[:80]


def try_parse_json(text: str) -> Any | None:
    try:
        return json.loads(text)
    except Exception:
        return None


def redact_headers(headers: dict[str, str]) -> dict[str, str]:
    result: dict[str, str] = {}
    for key, value in headers.items():
        if SENSITIVE_KEY_PATTERN.search(key):
            result[key] = "[redacted]"
        elif key.lower() == "authorization":
            result[key] = "[redacted]"
        else:
            result[key] = value
    return result


def redact_text(text: str) -> str:
    if SENSITIVE_VALUE_PATTERN.search(text):
        return "[redacted]"
    return text[:4000]


def redact_data(value: Any, key_path: str = "") -> Any:
    if isinstance(value, dict):
        result: dict[str, Any] = {}
        for key, item in value.items():
            child_path = f"{key_path}.{key}" if key_path else str(key)
            if SENSITIVE_KEY_PATTERN.search(str(key)):
                result[key] = "[redacted]"
            else:
                result[key] = redact_data(item, child_path)
        return result
    if isinstance(value, list):
        return [redact_data(item, key_path) for item in value]
    if isinstance(value, str):
        if SENSITIVE_KEY_PATTERN.search(key_path) or SENSITIVE_VALUE_PATTERN.search(value):
            return "[redacted]"
        return value[:4000]
    return value


def endpoint_is_ready(cdp_url: str) -> bool:
    try:
        with urlopen(f"{cdp_url.rstrip('/')}/json/version", timeout=2) as response:
            return response.status == 200
    except URLError:
        return False
    except Exception:
        return False


def default_output_dir(repo_root: Path) -> Path:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return repo_root / "App_State" / "portal_observer" / stamp


def detect_chrome_path() -> str | None:
    candidates = [
        "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
        "/Applications/Google Chrome Canary.app/Contents/MacOS/Google Chrome Canary",
    ]
    for candidate in candidates:
        if Path(candidate).exists():
            return candidate
    return None


def launch_chrome(chrome_path: str, cdp_url: str, profile_dir: Path, open_url: str) -> subprocess.Popen[str]:
    profile_dir.mkdir(parents=True, exist_ok=True)
    port = cdp_url.rsplit(":", 1)[-1].strip().rstrip("/")
    command = [
        chrome_path,
        f"--remote-debugging-port={port}",
        f"--user-data-dir={profile_dir}",
        "--no-first-run",
        "--no-default-browser-check",
        "--new-window",
        open_url,
    ]
    return subprocess.Popen(command, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, text=True)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Observe an Azure Portal Chrome tab and record progressive field changes.")
    parser.add_argument("--cdp-url", default=DEFAULT_CDP_URL, help="Chrome DevTools endpoint, e.g. http://127.0.0.1:9222")
    parser.add_argument("--target-url-substring", default=DEFAULT_TARGET_URL, help="Attach to the first tab whose URL contains this text.")
    parser.add_argument("--output-dir", default="", help="Directory for observer output. Defaults to App_State/portal_observer/<timestamp>.")
    parser.add_argument("--poll-seconds", type=float, default=DEFAULT_POLL_SECONDS, help="Polling interval while waiting for Chrome or the target tab.")
    parser.add_argument("--launch-chrome", action="store_true", help="Launch a dedicated Chrome window with remote debugging enabled.")
    parser.add_argument("--chrome-path", default="", help="Explicit Chrome binary path.")
    parser.add_argument("--open-url", default="https://portal.azure.com", help="URL to open when launching Chrome.")
    parser.add_argument("--chrome-profile-dir", default="", help="Profile dir for the launched Chrome window.")
    return parser


async def async_main(args: argparse.Namespace) -> int:
    repo_root = Path(__file__).resolve().parents[1]
    output_dir = Path(args.output_dir).expanduser() if args.output_dir else default_output_dir(repo_root)
    profile_dir = Path(args.chrome_profile_dir).expanduser() if args.chrome_profile_dir else output_dir / "chrome-profile"
    config = ObserverConfig(
        cdp_url=args.cdp_url,
        target_url_substring=args.target_url_substring,
        output_dir=output_dir,
        poll_seconds=max(args.poll_seconds, 0.5),
        launch_chrome=bool(args.launch_chrome),
        chrome_path=args.chrome_path or detect_chrome_path(),
        open_url=args.open_url,
        chrome_profile_dir=profile_dir,
    )
    config.output_dir.mkdir(parents=True, exist_ok=True)
    observer = AzurePortalObserver(config)
    observer.initialize_output()

    chrome_process: subprocess.Popen[str] | None = None
    if config.launch_chrome:
        if not config.chrome_path:
            print("[observer] Chrome was not found. Pass --chrome-path explicitly.", file=sys.stderr, flush=True)
            return 2
        print(f"[observer] Launching Chrome: {config.chrome_path}", flush=True)
        chrome_process = launch_chrome(config.chrome_path, config.cdp_url, config.chrome_profile_dir, config.open_url)

    loop = asyncio.get_running_loop()
    for signame in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(signame, observer.stop)
        except NotImplementedError:
            pass

    try:
        async with async_playwright() as playwright:
            browser = await observer.wait_for_browser(playwright)
            page = await observer.wait_for_target_page(browser)
            await observer.attach(page)
            print(f"[observer] Recording to: {config.output_dir}", flush=True)
            print("[observer] Ready. Start your Azure Portal steps now.", flush=True)
            await observer.wait_until_stopped()
            await browser.close()
    finally:
        if chrome_process and chrome_process.poll() is None:
            chrome_process.terminate()
    return 0


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return asyncio.run(async_main(args))


if __name__ == "__main__":
    raise SystemExit(main())
