import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CLOUDS_DIR = ROOT / "Clouds"
OUTPUT_DIR = ROOT / "App_Frontend" / "catalogs"
ICON_EXTENSIONS = {".svg", ".png", ".jpg", ".jpeg"}


def sanitize_resource_name(filename: str) -> str:
    name = re.sub(r"^\d+-icon-service-", "", filename)
    name = re.sub(r"\.(svg|png|jpg|jpeg)$", "", name, flags=re.IGNORECASE)
    name = re.sub(r"\(.*?\)", "", name)
    name = name.replace("-", " ")
    name = re.sub(r"\s+", " ", name).strip()
    return name


def build_cloud_catalog(cloud_dir: Path) -> dict:
    icons_root = cloud_dir / "Icons"
    if not icons_root.exists() or not icons_root.is_dir():
        return {}

    catalog = {}
    category_dirs = sorted([path for path in icons_root.iterdir() if path.is_dir()], key=lambda path: path.name.lower())

    for category_dir in category_dirs:
        resources = []
        icon_files = sorted(
            [path for path in category_dir.iterdir() if path.is_file() and path.suffix.lower() in ICON_EXTENSIONS],
            key=lambda path: path.name.lower()
        )

        for icon_path in icon_files:
            resources.append({
                "name": sanitize_resource_name(icon_path.name),
                "icon": icon_path.name
            })

        resources.sort(key=lambda resource: resource["name"].lower())
        catalog[category_dir.name] = resources

    return catalog


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

    if not CLOUDS_DIR.exists():
        return

    cloud_dirs = sorted([path for path in CLOUDS_DIR.iterdir() if path.is_dir()], key=lambda path: path.name.lower())
    for cloud_dir in cloud_dirs:
        catalog = build_cloud_catalog(cloud_dir)
        output_path = OUTPUT_DIR / f"{cloud_dir.name.lower()}.json"
        output_path.write_text(json.dumps(catalog, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
