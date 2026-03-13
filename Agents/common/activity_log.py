from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import re
from threading import Lock
from typing import Any, Mapping
from urllib.parse import urlsplit

DEFAULT_APP_STATE_DIR = Path("/workspace/App_State")
DEFAULT_LOGS_DIR_NAME = "logs"
DEFAULT_LOG_FILE_PREFIX = "application"
DEFAULT_LOG_FILE_EXTENSION = ".log"
DEFAULT_MAX_LOG_FILE_BYTES = 50 * 1024 * 1024
DEFAULT_MAX_VALUE_CHARS = 800
DEFAULT_MAX_MESSAGE_CHARS = 1600
DEFAULT_MAX_DETAIL_ITEMS = 8
DEFAULT_MAX_NESTED_DETAIL_ITEMS = 4

_MASKED_KEY_NAMES = {
    "azuretenantid",
    "tenantid",
    "azureclientid",
    "clientid",
    "azureclientsecret",
    "clientsecret",
    "azuresubscriptionid",
    "subscriptionid",
    "aifoundryendpoint",
    "foundryendpoint",
    "azurefoundryendpoint",
    "endpoint",
}

_PREFERRED_DETAIL_KEYS = {
    "reason": 0,
    "error": 1,
    "message": 2,
    "ok": 3,
    "skipped": 4,
    "created": 5,
    "settingsupdated": 6,
    "savetrigger": 7,
    "resourcecount": 8,
    "connectioncount": 9,
    "statehash": 10,
    "threadid": 11,
    "chatagentid": 12,
    "iacagentid": 13,
    "agentid": 14,
}

_LOG_LOCK = Lock()
_LOG_STATE: dict[str, dict[str, Any]] = {}


def _normalize_key_name(value: Any) -> str:
    return "".join(character for character in str(value or "").strip().lower() if character.isalnum())


def _clean_fragment(value: Any, fallback: str) -> str:
    raw_text = "" if value is None else str(value)
    text = " ".join(raw_text.split())
    return text or fallback


def _truncate_text(value: str, *, max_chars: int) -> str:
    text = str(value or "")
    if len(text) <= max_chars:
        return text
    return text[: max_chars - 3] + "..."


def _humanize_key(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return "detail"
    text = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    text = text.replace("_", " ").replace("-", " ")
    return " ".join(text.split()).strip().lower()


def _ordered_mapping_items(value: Mapping[str, Any]) -> list[tuple[str, Any]]:
    items = [(str(key), item) for key, item in value.items()]

    def _sort_key(item: tuple[str, Any]) -> tuple[int, str]:
        normalized = _normalize_key_name(item[0])
        priority = _PREFERRED_DETAIL_KEYS.get(normalized, 100)
        return priority, item[0].lower()

    return sorted(items, key=_sort_key)


def _mask_keep_ends(value: Any, *, keep_start: int, keep_end: int) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= keep_start + keep_end + 3:
        return "***"
    return f"{text[:keep_start]}...{text[-keep_end:]}"


def _mask_troubleshooting_value(value: Any) -> str:
    text = str(value or "").strip()
    if not text:
        return ""

    if "://" in text:
        parsed = urlsplit(text)
        host = parsed.netloc or parsed.path
        host_hint = _mask_keep_ends(host, keep_start=4, keep_end=4)
        last_path_segment = parsed.path.rstrip("/").split("/")[-1].strip()
        prefix = f"{parsed.scheme}://" if parsed.scheme else ""
        if last_path_segment and last_path_segment != host:
            return _truncate_text(f"{prefix}{host_hint}/.../{last_path_segment}", max_chars=120)
        return _truncate_text(f"{prefix}{host_hint}", max_chars=120)

    return _mask_keep_ends(text, keep_start=4, keep_end=4)


def _sanitize_for_log(value: Any, key_hint: str = "") -> Any:
    normalized_key = _normalize_key_name(key_hint)
    if normalized_key in _MASKED_KEY_NAMES:
        return _mask_troubleshooting_value(value)

    if isinstance(value, Mapping):
        cleaned: dict[str, Any] = {}
        for raw_key, raw_value in value.items():
            safe_key = str(raw_key)
            cleaned[safe_key] = _sanitize_for_log(raw_value, safe_key)
        return cleaned

    if isinstance(value, list):
        return [_sanitize_for_log(item, key_hint) for item in value[:20]]

    if isinstance(value, str):
        return _truncate_text(value.strip(), max_chars=DEFAULT_MAX_VALUE_CHARS)

    return value


def _normalize_level(level: str | None) -> str:
    value = str(level or "info").strip().lower()
    if value in {"warning", "warn"}:
        return "warning"
    if value in {"error", "failed", "failure", "fatal"}:
        return "error"
    return "info"


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def _state_key(app_state_dir: Path) -> str:
    try:
        return str(app_state_dir.resolve())
    except Exception:
        return str(app_state_dir)


def _ensure_state_for_root(app_state_dir: Path) -> dict[str, Any]:
    key = _state_key(app_state_dir)
    state = _LOG_STATE.get(key)
    if not isinstance(state, dict):
        state = {
            "activePath": None,
        }
        _LOG_STATE[key] = state
    return state


def _new_log_file_path(logs_dir: Path) -> Path:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    base = f"{DEFAULT_LOG_FILE_PREFIX}-{stamp}"
    candidate = logs_dir / f"{base}{DEFAULT_LOG_FILE_EXTENSION}"
    index = 1
    while candidate.exists():
        candidate = logs_dir / f"{base}-{index:02d}{DEFAULT_LOG_FILE_EXTENSION}"
        index += 1
    return candidate


def _latest_log_file(logs_dir: Path) -> Path | None:
    candidates = sorted(logs_dir.glob(f"{DEFAULT_LOG_FILE_PREFIX}-*{DEFAULT_LOG_FILE_EXTENSION}"))
    if not candidates:
        return None
    return candidates[-1]


def _safe_file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except Exception:
        return 0


def _resolve_log_path(
    app_state_dir: Path,
    *,
    max_file_bytes: int,
) -> tuple[Path, str | None]:
    state = _ensure_state_for_root(app_state_dir)
    logs_dir = app_state_dir / DEFAULT_LOGS_DIR_NAME

    active_path = state.get("activePath")
    if isinstance(active_path, Path):
        if active_path.exists() and _safe_file_size(active_path) < max_file_bytes:
            return active_path, None
        if active_path.exists() and _safe_file_size(active_path) >= max_file_bytes:
            previous_name = active_path.name
            next_path = _new_log_file_path(logs_dir)
            state["activePath"] = next_path
            return next_path, previous_name

    latest = _latest_log_file(logs_dir)
    if isinstance(latest, Path) and latest.exists() and _safe_file_size(latest) < max_file_bytes:
        state["activePath"] = latest
        return latest, None

    previous_name = latest.name if isinstance(latest, Path) and latest.exists() else None
    next_path = _new_log_file_path(logs_dir)
    state["activePath"] = next_path
    return next_path, previous_name


def _stringify_value(value: Any, *, depth: int = 0) -> str:
    if isinstance(value, Mapping):
        if not value:
            return "none"
        ordered = _ordered_mapping_items(value)
        limit = DEFAULT_MAX_NESTED_DETAIL_ITEMS if depth > 0 else DEFAULT_MAX_DETAIL_ITEMS
        parts: list[str] = []
        for idx, (key, item) in enumerate(ordered):
            if idx >= limit:
                parts.append(f"+{len(ordered) - limit} more")
                break
            parts.append(f"{_humanize_key(key)}: {_stringify_value(item, depth=depth + 1)}")
        return ", ".join(parts)

    if isinstance(value, list):
        if not value:
            return "none"
        limit = 3 if depth > 0 else 5
        parts = [_stringify_value(item, depth=depth + 1) for item in value[:limit]]
        if len(value) > limit:
            parts.append(f"+{len(value) - limit} more")
        return ", ".join(parts)

    if isinstance(value, bool):
        return "yes" if value else "no"

    if value is None:
        return "none"

    return _truncate_text(_clean_fragment(value, ""), max_chars=DEFAULT_MAX_VALUE_CHARS)


def _format_details(details: Mapping[str, Any] | None) -> str:
    if not isinstance(details, Mapping) or not details:
        return ""

    sanitized = _sanitize_for_log(dict(details))
    if not isinstance(sanitized, Mapping):
        return _truncate_text(_stringify_value(sanitized, depth=0), max_chars=DEFAULT_MAX_MESSAGE_CHARS)

    parts: list[str] = []
    ordered = _ordered_mapping_items(sanitized)
    for idx, (key, value) in enumerate(ordered):
        if idx >= DEFAULT_MAX_DETAIL_ITEMS:
            parts.append(f"+{len(ordered) - DEFAULT_MAX_DETAIL_ITEMS} more details")
            break
        parts.append(f"{_humanize_key(key)}: {_stringify_value(value, depth=0)}")

    return _truncate_text("; ".join(parts), max_chars=DEFAULT_MAX_MESSAGE_CHARS)


def _build_message(
    *,
    step: str,
    project_id: str | None,
    details: Mapping[str, Any] | None,
) -> str:
    message_parts: list[str] = []

    safe_step = _clean_fragment(step, "")
    if safe_step:
        message_parts.append(safe_step)

    safe_project_id = str(project_id or "").strip()
    if safe_project_id:
        message_parts.append(f"project: {safe_project_id}")

    detail_text = _format_details(details)
    if detail_text:
        message_parts.append(detail_text)

    return " | ".join(message_parts) if message_parts else "-"


def _build_log_line(
    *,
    event_type: str,
    category: str,
    level: str,
    step: str,
    source: str,
    project_id: str | None,
    details: Mapping[str, Any] | None,
) -> str:
    timestamp = _now_utc_iso()
    safe_event_type = _clean_fragment(event_type, "application.event")
    safe_category = _clean_fragment(category, "application")
    safe_level = _normalize_level(level)
    safe_source = _clean_fragment(source, "backend")
    message = _build_message(step=step, project_id=project_id, details=details)
    return f"{timestamp}: {safe_event_type}: {safe_category}: {safe_level}: {safe_source}: {message}\n"


def _append_log_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(line)


def log_activity(
    *,
    event_type: str,
    category: str = "application",
    level: str = "info",
    step: str = "",
    source: str = "backend",
    project_id: str | None = None,
    details: Mapping[str, Any] | None = None,
    app_state_dir: Path | None = None,
    max_file_bytes: int = DEFAULT_MAX_LOG_FILE_BYTES,
) -> Path | None:
    try:
        app_state_root = Path(app_state_dir) if app_state_dir is not None else DEFAULT_APP_STATE_DIR
        logs_dir = app_state_root / DEFAULT_LOGS_DIR_NAME

        with _LOG_LOCK:
            logs_dir.mkdir(parents=True, exist_ok=True)
            target_path, rotated_from = _resolve_log_path(
                app_state_root,
                max_file_bytes=max(1024 * 1024, int(max_file_bytes or DEFAULT_MAX_LOG_FILE_BYTES)),
            )

            if rotated_from:
                _append_log_line(
                    target_path,
                    _build_log_line(
                        event_type="application.log.rotate",
                        category="logging",
                        level="info",
                        step="rotate",
                        source="logger",
                        project_id=None,
                        details={
                            "rotatedFrom": rotated_from,
                            "rotatedTo": target_path.name,
                            "maxFileBytes": max_file_bytes,
                        },
                    ),
                )

            _append_log_line(
                target_path,
                _build_log_line(
                    event_type=str(event_type or "application.event").strip() or "application.event",
                    category=str(category or "application").strip() or "application",
                    level=level,
                    step=str(step or "").strip() or str(event_type or "application.event").strip(),
                    source=str(source or "backend").strip() or "backend",
                    project_id=str(project_id or "").strip() or None,
                    details=details if isinstance(details, Mapping) else None,
                ),
            )
            return target_path
    except Exception:
        return None


def resolve_logs_dir(app_state_dir: Path | None = None) -> Path:
    root = Path(app_state_dir) if app_state_dir is not None else DEFAULT_APP_STATE_DIR
    return root / DEFAULT_LOGS_DIR_NAME
