from pathlib import Path
import json
import re
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

app = FastAPI()

WORKSPACE_ROOT = Path("/workspace")
APP_STATE_DIR = WORKSPACE_ROOT / "App_State"
PROJECTS_DIR = WORKSPACE_ROOT / "Projects"


class AppSettingsPayload(BaseModel):
    settings: dict


class ProjectMeta(BaseModel):
    id: str
    name: str
    cloud: str


class ProjectSettingsPayload(BaseModel):
    project: ProjectMeta
    settings: dict


class ProjectSavePayload(BaseModel):
    project: ProjectMeta
    canvasState: dict = {}


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


@app.post("/api/settings/app")
def save_app_settings(body: AppSettingsPayload):
    try:
        APP_STATE_DIR.mkdir(parents=True, exist_ok=True)
        target = APP_STATE_DIR / "app.settings.env"
        target.write_text(to_env_lines(body.settings), encoding="utf-8")
        return {"ok": True, "path": str(target.relative_to(WORKSPACE_ROOT))}
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Failed to save app settings: {exc}") from exc


@app.post("/api/settings/project")
def save_project_settings(body: ProjectSettingsPayload):
    try:
        project_folder_name = sanitize_segment(body.project.name, sanitize_segment(body.project.id, "project"))
        target_dir = PROJECTS_DIR / project_folder_name
        target_dir.mkdir(parents=True, exist_ok=True)

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


@app.post("/api/project/save")
def save_project_snapshot(body: ProjectSavePayload):
    try:
        project_folder_name = sanitize_segment(body.project.name, sanitize_segment(body.project.id, "project"))
        project_dir = PROJECTS_DIR / project_folder_name
        architecture_dir = project_dir / "Architecture"

        architecture_dir.mkdir(parents=True, exist_ok=True)

        metadata_payload = {
            "id": body.project.id,
            "name": body.project.name,
            "cloud": body.project.cloud,
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
