"""Build a CycloneDX 1.6 SBOM from pinned Go and Python environment inventories."""

from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote
from uuid import NAMESPACE_URL, uuid5


def read_concatenated_json(path: Path) -> list[dict]:
    text = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    values = []
    offset = 0
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset >= len(text):
            break
        value, offset = decoder.raw_decode(text, offset)
        if not isinstance(value, dict):
            raise ValueError("Go module inventory must contain JSON objects")
        values.append(value)
    return values


def component(component_type: str, name: str, version: str, purl_type: str) -> dict:
    normalized = name.strip()
    version = version.strip()
    if not normalized or not version:
        raise ValueError("component name and version must be non-empty")
    purl = f"pkg:{purl_type}/{quote(normalized, safe='/-._~')}@{quote(version, safe='-._~+')}"
    return {
        "type": "library",
        "name": normalized,
        "version": version,
        "purl": purl,
        "bom-ref": purl,
        "scope": "required",
    }


def build_bom(go_modules: list[dict], python_packages: list[dict], version: str) -> dict:
    if not isinstance(version, str) or not version.strip():
        raise ValueError("release version is required")
    components = []
    for module in go_modules:
        if module.get("Main"):
            continue
        name, module_version = module.get("Path"), module.get("Version")
        if name and module_version:
            components.append(component("library", name, module_version, "golang"))
    for package in python_packages:
        name, package_version = package.get("name"), package.get("version")
        if name and package_version:
            components.append(component("library", name.lower().replace("_", "-"), package_version, "pypi"))
    components.sort(key=lambda item: item["purl"])
    if len({item["purl"] for item in components}) != len(components):
        raise ValueError("duplicate package component in dependency inventory")
    root_purl = f"pkg:github/mojtaba-arvin/voxpipe@{quote(version, safe='-._~+')}"
    serial_seed = version + "\n" + "\n".join(item["purl"] for item in components)
    return {
        "bomFormat": "CycloneDX",
        "specVersion": "1.6",
        "serialNumber": "urn:uuid:" + str(uuid5(NAMESPACE_URL, serial_seed)),
        "version": 1,
        "metadata": {
            "component": {
                "type": "application",
                "name": "VoxPipe",
                "version": version,
                "purl": root_purl,
                "bom-ref": root_purl,
            },
            "tools": {"components": [{
                "type": "application", "name": "voxpipe-sbom-builder", "version": "1.0.0"
            }]},
        },
        "components": components,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--go-modules", type=Path, required=True)
    parser.add_argument("--python-packages", type=Path, required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    packages = json.loads(args.python_packages.read_text(encoding="utf-8"))
    if not isinstance(packages, list) or any(not isinstance(item, dict) for item in packages):
        raise SystemExit("Python package inventory must be a JSON array of package objects")
    bom = build_bom(read_concatenated_json(args.go_modules), packages, args.version)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bom, indent=2, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
