#!/usr/bin/env python3
import json
import os
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

# Provide a permissive SSL context for macOS
try:
    import certifi
    _SSL_CTX = ssl.create_default_context(cafile=certifi.where())
except ImportError:
    _SSL_CTX = ssl.create_default_context()
    _SSL_CTX.check_hostname = False
    _SSL_CTX.verify_mode = ssl.CERT_NONE

BASE = Path(__file__).parent
CATALOG_PATH = BASE / "resource_catalog.json"
BICEP_DIR = BASE / "ResourceSchema" / "Bicep"
TF_DIR = BASE / "ResourceSchema" / "Terraform"
INDEX_CACHE = Path("/tmp/bicep_az_index.json")

BICEP_RAW = "https://raw.githubusercontent.com/Azure/bicep-types-az/main/generated"

def load_json(path): return json.loads(Path(path).read_text())
def save_json(path, data): Path(path).write_text(json.dumps(data, indent=2))

def fetch_url(url: str, retries=3) -> bytes:
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "agentic-cloud-architect/1.0"})
            with urllib.request.urlopen(req, timeout=30, context=_SSL_CTX) as r:
                return r.read()
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if attempt == retries - 1:
                raise
        except Exception:
            if attempt == retries - 1:
                raise
        time.sleep(1.5 ** attempt)

def resource_filename(bicep_type: str) -> str:
    type_part = bicep_type.split("@")[0] if "@" in bicep_type else bicep_type
    return type_part.lower().replace("/", "-").replace(".", "_", 1).replace(".", "-") + ".json"

# --- Bicep Reference Resolution ---

def get_ref_index(ref_str: str) -> int:
    return int(ref_str.split("#/")[-1])

def resolve_type(type_info, types_list, depth=0):
    if depth > 10:
        return {"$type": "MaxDepthReached"}
    
    if "$ref" in type_info:
        idx = get_ref_index(type_info["$ref"])
        actual_type = types_list[idx]
        return resolve_type(actual_type, types_list, depth + 1)
        
    t_type = type_info.get("$type")
    
    if t_type == "ObjectType":
        props = {}
        for prop_name, prop_val in type_info.get("properties", {}).items():
            props[prop_name] = {
                "flags": prop_val.get("flags", 0),
                "type": resolve_type(prop_val.get("type", {}), types_list, depth + 1)
            }
            if "description" in prop_val:
                props[prop_name]["description"] = prop_val["description"]
        
        result = {"$type": "ObjectType"}
        if props:
            result["properties"] = props
            
        # Merge additional properties
        if "additionalProperties" in type_info:
            result["additionalProperties"] = resolve_type(type_info["additionalProperties"], types_list, depth + 1)
            
        return result
        
    elif t_type == "ArrayType":
        return {
            "$type": "ArrayType",
            "itemType": resolve_type(type_info.get("itemType", {}), types_list, depth + 1)
        }
        
    elif t_type == "UnionType":
        elements = [resolve_type(e, types_list, depth + 1) for e in type_info.get("elements", [])]
        return {
            "$type": "UnionType",
            "elements": elements
        }
        
    elif t_type == "ResourceType":
        return {
            "$type": "ResourceType",
            "name": type_info.get("name"),
            "body": resolve_type(type_info.get("body", {}), types_list, depth + 1)
        }
        
    # StringType, IntType, BooleanType, StringLiteralType etc
    return type_info.copy()

# --- Bicep Processing ---

def get_index() -> dict:
    if INDEX_CACHE.exists():
        age = time.time() - INDEX_CACHE.stat().st_mtime
        if age < 86400:
            print("  Using cached bicep index.json")
            return load_json(INDEX_CACHE)["resources"]

    print("  Downloading bicep-types-az index.json …")
    data = fetch_url(f"{BICEP_RAW}/index.json")
    if not data:
        sys.exit("ERROR: could not fetch bicep-types-az index.json")
    parsed = json.loads(data)
    save_json(INDEX_CACHE, parsed)
    return parsed["resources"]

def fetch_bicep_schemas(catalog: dict):
    BICEP_DIR.mkdir(parents=True, exist_ok=True)
    print("\n=== Bicep Schemas ===")
    index = get_index()
    types_cache: dict[str, list] = {}

    deployable = {name: e for name, e in catalog.items() if e.get("deployable") is True}
    fetched = skipped = missing = 0

    for name, entry in sorted(deployable.items()):
        bicep_type = entry.get("bicepType", "")
        if not bicep_type:
            missing += 1
            continue

        out_path = BICEP_DIR / resource_filename(bicep_type)
        if out_path.exists():
            skipped += 1
            continue

        ref_entry = index.get(bicep_type)
        if not ref_entry:
            candidates = [k for k in index if k.lower() == bicep_type.lower()]
            ref_entry = index.get(candidates[0]) if candidates else None

        if not ref_entry:
            print(f"  SKIP (not in bicep index): {name:45}")
            missing += 1
            continue

        ref = ref_entry.get("$ref", "")
        if "#" not in ref:
            missing += 1
            continue

        file_path, idx_str = ref.rsplit("#/", 1)
        type_idx = int(idx_str)

        if file_path not in types_cache:
            url = f"{BICEP_RAW}/{file_path}"
            raw = fetch_url(url)
            if raw is None:
                print(f"  NOT FOUND: {file_path}")
                types_cache[file_path] = []
            else:
                types_cache[file_path] = json.loads(raw)
            time.sleep(0.1)

        types_list = types_cache[file_path]
        if not types_list or type_idx >= len(types_list):
            missing += 1
            continue

        resolved_def = resolve_type(types_list[type_idx], types_list)

        schema = {
            "resourceName":  name,
            "bicepType":     bicep_type,
            "terraformType": entry.get("terraformType", ""),
            "category":      entry.get("category", ""),
            "schemaRef":     entry.get("schemaRef", ""),
            "definition":    resolved_def,
        }
        save_json(out_path, schema)
        print(f"  OK  {name:45} -> {out_path.name}")
        fetched += 1

    print(f"\nBicep: {fetched} fetched, {skipped} already existed, {missing} not found/skipped")

# --- Terraform Processing ---

TF_MAIN_TF = """\
terraform {
  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {}
  skip_provider_registration = true
}
"""

def fetch_terraform_schemas(catalog: dict):
    TF_DIR.mkdir(parents=True, exist_ok=True)
    print("\n=== Terraform Schemas ===")

    full_schema_cache = Path("/tmp/azurerm_tf_schema.json")
    if full_schema_cache.exists() and (time.time() - full_schema_cache.stat().st_mtime) < 86400:
        print("  Using cached azurerm provider schema")
        full_schema = load_json(full_schema_cache)
    else:
        print("  Initialising Terraform + downloading azurerm provider (may take 1-3 min) …")
        with tempfile.TemporaryDirectory() as tmp:
            main_tf = os.path.join(tmp, "main.tf")
            Path(main_tf).write_text(TF_MAIN_TF)

            subprocess.run(["terraform", "init", "-no-color"], cwd=tmp, capture_output=True, text=True)
            schema_proc = subprocess.run(["terraform", "providers", "schema", "-json"], cwd=tmp, capture_output=True, text=True)
            
            if schema_proc.returncode != 0:
                print("  terraform providers schema failed:\n", schema_proc.stderr[:800])
                sys.exit(1)

            full_schema = json.loads(schema_proc.stdout)
            save_json(full_schema_cache, full_schema)

    provider_schemas = full_schema.get("provider_schemas", {}).get("registry.terraform.io/hashicorp/azurerm", {})
    resource_schemas = provider_schemas.get("resource_schemas", {})

    deployable = {name: e for name, e in catalog.items() if e.get("deployable") is True}
    saved = skipped = missing = 0

    for name, entry in sorted(deployable.items()):
        tf_type = entry.get("terraformType", "")
        if not tf_type:
            missing += 1
            continue

        out_path = TF_DIR / f"{tf_type}.json"
        
        if out_path.exists():
            skipped += 1
            continue

        tf_schema = resource_schemas.get(tf_type)
        if not tf_schema:
            print(f"  SKIP (not in provider): {name:45} {tf_type}")
            missing += 1
            continue

        schema = {
            "resourceName":  name,
            "bicepType":     entry.get("bicepType", ""),
            "terraformType":  tf_type,
            "category":      entry.get("category", ""),
            "schemaRef":     entry.get("schemaRef", ""),
            "definition":    tf_schema,
        }
        save_json(out_path, schema)
        print(f"  OK  {name:45} -> {out_path.name}")
        saved += 1

    print(f"\nTerraform: {saved} saved, {skipped} already existed, {missing} not found/skipped")

def main():
    catalog = load_json(CATALOG_PATH)
    fetch_bicep_schemas(catalog)
    fetch_terraform_schemas(catalog)

if __name__ == "__main__":
    main()
