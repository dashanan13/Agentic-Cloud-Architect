from pathlib import Path
import json
import os
import re
import shutil
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

WORKSPACE_ROOT = Path("/workspace")
APP_STATE_DIR = WORKSPACE_ROOT / "App_State"
PROJECTS_DIR = WORKSPACE_ROOT / "Projects"
DEFAULT_TEMPLATE_DIR = PROJECTS_DIR / "Default"


class AppSettingsPayload(BaseModel):
    settings: dict


class ProjectMeta(BaseModel):
    id: str
    name: str
    cloud: str
    lastSaved: int | None = None


class ProjectSettingsPayload(BaseModel):
    project: ProjectMeta
    settings: dict


class ProjectSavePayload(BaseModel):
    project: ProjectMeta
    canvasState: dict = {}


def read_json_file(path: Path, default):
    try:
        if not path.exists():
            return default
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def collect_project_entries() -> list[dict]:
    entries = []
    if not PROJECTS_DIR.exists():
        return entries

    for project_dir in PROJECTS_DIR.iterdir():
        if not project_dir.is_dir():
            continue

        architecture_dir = project_dir / "Architecture"
        metadata_path = architecture_dir / "project.metadata.json"
        state_path = architecture_dir / "canvas.state.json"

        metadata = read_json_file(metadata_path, {})
        if not isinstance(metadata, dict):
            metadata = {}

        project_id = str(metadata.get("id") or "").strip()
        name = str(metadata.get("name") or "").strip()
        cloud = str(metadata.get("cloud") or "").strip()

        if not project_id or not name or cloud not in {"Azure", "AWS", "GCP"}:
            continue

        state_payload = read_json_file(state_path, {})
        if not isinstance(state_payload, dict):
            state_payload = {}

        last_saved = metadata.get("lastSaved")
        if not isinstance(last_saved, (int, float)):
            project_meta = state_payload.get("project") if isinstance(state_payload.get("project"), dict) else {}
            candidate = project_meta.get("lastSaved")
            if isinstance(candidate, (int, float)):
                last_saved = candidate
            elif state_path.exists():
                last_saved = int(state_path.stat().st_mtime * 1000)
            else:
                last_saved = int(metadata_path.stat().st_mtime * 1000) if metadata_path.exists() else 0

        entries.append(
            {
                "id": project_id,
                "name": name,
                "cloud": cloud,
                "lastSaved": int(last_saved),
                "projectDir": project_dir,
                "metadataPath": metadata_path,
                "statePath": state_path,
            }
        )

    entries.sort(key=lambda item: item["lastSaved"], reverse=True)
    return entries


def find_project_entry(project_id: str):
    project_id = str(project_id or "").strip()
    if not project_id:
        return None

    for entry in collect_project_entries():
        if entry["id"] == project_id:
            return entry
    return None


def sanitize_segment(value: str, fallback: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip())
    cleaned = cleaned.strip("-._")
    return cleaned or fallback


def to_env_lines(payload: dict) -> str:
    lines = []
    for key, value in payload.items():
        key_text = str(key)
        snake = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", key_text)
        env_key = re.sub(r"[^a-zA-Z0-9_]", "_", snake).upper()
        env_value = "" if value is None else str(value)
        lines.append(f"{env_key}={env_value}")
    return "\n".join(lines) + "\n"


def snake_to_camel(value: str) -> str:
    parts = str(value or "").lower().split("_")
    if not parts:
        return ""
    return parts[0] + "".join(part.capitalize() for part in parts[1:])


def parse_env_file(path: Path) -> dict:
    if not path.exists():
        return {}

    payload = {}
    lines = path.read_text(encoding="utf-8").splitlines()
    for line in lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        payload[snake_to_camel(key.strip())] = value.strip()

    return payload


def ensure_project_structure(project_dir: Path) -> None:
    if not DEFAULT_TEMPLATE_DIR.exists():
        project_dir.mkdir(parents=True, exist_ok=True)
        return

    if not project_dir.exists():
        shutil.copytree(DEFAULT_TEMPLATE_DIR, project_dir)
        return

    for root, dirs, files in os.walk(DEFAULT_TEMPLATE_DIR):
        rel_root = Path(root).relative_to(DEFAULT_TEMPLATE_DIR)
        target_root = project_dir / rel_root
        target_root.mkdir(parents=True, exist_ok=True)

        for dirname in dirs:
            (target_root / dirname).mkdir(parents=True, exist_ok=True)

        for filename in files:
            source_path = Path(root) / filename
            target_path = target_root / filename
            if not target_path.exists():
                shutil.copy2(source_path, target_path)


@app.post("/api/settings/app")
def save_app_settings(body: AppSettingsPayload):
    try:
        APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        target = APP_STATE_DIR / "app.settings.env"
        target.write_text(to_env_lines(body.settings), encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save app settings: {exc}") from exc


@app.get("/api/settings/app")
def get_app_settings():
    target = APP_STATE_DIR / "app.settings.env"
    return {
        "settings": parse_env_file(target),
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


@app.post("/api/settings/project")
def save_project_settings(body: ProjectSettingsPayload):
    try:
        project_folder_name = sanitize_segment(body.project.name, sanitize_segment(body.project.id, "project"))
        target_dir = PROJECTS_DIR / project_folder_name
        ensure_project_structure(target_dir)

        payload = {
            "project_id": body.project.id,
            "project_name": body.project.name,
            "project_cloud": body.project.cloud,
            **body.settings,
        }

        target = target_dir / "project.settings.env"
        target.write_text(to_env_lines(payload), encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save project settings: {exc}") from exc


@app.get("/api/settings/project/{project_id}")
def get_project_settings(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    target = entry["projectDir"] / "project.settings.env"
    return {
        "settings": parse_env_file(target),
        "path": str(target.relative_to(WORKSPACE_ROOT)) if target.exists() else None,
    }


@app.post("/api/project/save")
def save_project_snapshot(body: ProjectSavePayload):
    try:
        project_folder_name = sanitize_segment(body.project.name, sanitize_segment(body.project.id, "project"))
        project_dir = PROJECTS_DIR / project_folder_name
        architecture_dir = project_dir / "Architecture"

        ensure_project_structure(project_dir)
        architecture_dir.mkdir(parents=True, exist_ok=True)

        metadata_payload = {
            "id": body.project.id,
            "name": body.project.name,
            "cloud": body.project.cloud,
            "lastSaved": int(body.project.lastSaved) if isinstance(body.project.lastSaved, (int, float)) else 0,
        }

        (architecture_dir / "project.metadata.json").write_text(
            json.dumps(metadata_payload, indent=2),
            encoding="utf-8",
        )

        (architecture_dir / "canvas.state.json").write_text(
            json.dumps(body.canvasState or {}, indent=2),
            encoding="utf-8",
        )

        return {
            "ok": True,
            "projectPath": str(project_dir.relative_to(WORKSPACE_ROOT)),
            "files": [
                str((architecture_dir / "project.metadata.json").relative_to(WORKSPACE_ROOT)),
                str((architecture_dir / "canvas.state.json").relative_to(WORKSPACE_ROOT)),
            ],
        }
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save project snapshot: {exc}") from exc


@app.get("/api/projects")
def list_projects():
    entries = collect_project_entries()
    return {
        "projects": [
            {
                "id": entry["id"],
                "name": entry["name"],
                "cloud": entry["cloud"],
                "lastSaved": entry["lastSaved"],
            }
            for entry in entries
        ]
    }


@app.get("/api/project/{project_id}")
def get_project_snapshot(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    metadata = read_json_file(entry["metadataPath"], {})
    if not isinstance(metadata, dict):
        metadata = {}

    canvas_state = read_json_file(entry["statePath"], {})
    if not isinstance(canvas_state, dict):
        canvas_state = {}

    return {
        "project": {
            "id": entry["id"],
            "name": str(metadata.get("name") or entry["name"]),
            "cloud": str(metadata.get("cloud") or entry["cloud"]),
            "lastSaved": int(entry["lastSaved"]),
        },
        "canvasState": canvas_state,
    }


@app.delete("/api/project/{project_id}")
def delete_project_snapshot(project_id: str):
    entry = find_project_entry(project_id)
    if not entry:
        raise HTTPException(status_code=404, detail="Project not found")

    try:
        shutil.rmtree(entry["projectDir"])
        return {"ok": True}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to delete project: {exc}") from exc
