#!/usr/bin/env python3
"""Validate .yap packages and produce a deterministic static registry index."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import tarfile
import tomllib
import zipfile
from pathlib import Path, PurePosixPath

ID_RE = re.compile(r"[a-z0-9][a-z0-9_-]*\Z")
VERSION_RE = re.compile(r"[0-9]+(?:\.[0-9]+){0,2}(?:[-+][0-9A-Za-z.-]+)?\Z")


def safe_name(name: str) -> bool:
    path = PurePosixPath(name)
    return bool(name) and not path.is_absolute() and ".." not in path.parts


def manifest_from_archive(path: Path) -> dict:
    if zipfile.is_zipfile(path):
        with zipfile.ZipFile(path) as archive:
            names = archive.namelist()
            if any(not safe_name(name) for name in names):
                raise ValueError("unsafe ZIP path")
            try:
                raw = archive.read("plugin.toml")
            except KeyError as error:
                raise ValueError("plugin.toml must be at archive root") from error
    elif tarfile.is_tarfile(path):
        with tarfile.open(path, "r:*") as archive:
            members = archive.getmembers()
            if any(not safe_name(member.name) or member.issym() or member.islnk() for member in members):
                raise ValueError("unsafe TAR entry")
            # GNU tar commonly writes every root member as `./name`; it is
            # still the archive root, unlike `package/plugin.toml`.
            manifests = [
                member
                for member in members
                if PurePosixPath(member.name).parts == ("plugin.toml",)
            ]
            if len(manifests) != 1 or not manifests[0].isfile():
                raise ValueError("plugin.toml must be at archive root")
            extracted = archive.extractfile(manifests[0])
            if extracted is None:
                raise ValueError("cannot read plugin.toml")
            raw = extracted.read()
    else:
        raise ValueError("package is neither ZIP nor TAR archive")
    return tomllib.loads(raw.decode("utf-8"))


def version_key(version: str) -> tuple:
    """Sort supported SemVer-like versions; a stable release outranks prerelease."""
    core, separator, suffix = version, "", ""
    if "-" in version:
        core, suffix = version.split("-", maxsplit=1)
        separator = "-"
    elif "+" in version:
        core, _ = version.split("+", maxsplit=1)
    numbers = tuple(int(part) for part in core.split("."))
    return numbers, 1 if not separator else 0, suffix


def string_list(value: object, name: str, relative: Path) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list) or not all(isinstance(item, str) and item for item in value):
        raise ValueError(f"{relative}: compatibility.{name} must be an array of non-empty strings")
    if len(value) != len(set(value)):
        raise ValueError(f"{relative}: compatibility.{name} contains duplicates")
    return value


def build(packages_dir: Path, base_url: str) -> dict:
    packages = []
    if not packages_dir.exists():
        return {"format": 3, "packages": packages, "latest": {}}
    targets: set[tuple[str, str, str]] = set()
    for archive in sorted(packages_dir.rglob("*.yap")):
        relative = archive.relative_to(packages_dir)
        if len(relative.parts) == 2:
            package_id, filename = relative.parts
            version = archive.stem
            target = "any"
        elif len(relative.parts) == 3:
            package_id, version, filename = relative.parts
            target = archive.stem
            if not ID_RE.fullmatch(target):
                raise ValueError(f"{relative}: invalid target name")
        else:
            raise ValueError(f"{relative}: expected <id>/<version>.yap or <id>/<version>/<target>.yap")
        if not ID_RE.fullmatch(package_id):
            raise ValueError(f"{relative}: invalid package ID")
        if not VERSION_RE.fullmatch(version):
            raise ValueError(f"{relative}: invalid SemVer version")
        try:
            manifest = manifest_from_archive(archive)
        except (OSError, ValueError, tomllib.TOMLDecodeError, tarfile.TarError, zipfile.BadZipFile, UnicodeDecodeError) as error:
            raise ValueError(f"{relative}: {error}") from error
        if manifest.get("id") != package_id or manifest.get("version") != version:
            raise ValueError(f"{relative}: path must match plugin.toml id and version")
        entry = manifest.get("entry")
        if not isinstance(entry, str) or not safe_name(entry):
            raise ValueError(f"{relative}: invalid plugin entry")
        compatibility = manifest.get("compatibility", {})
        if not isinstance(compatibility, dict):
            raise ValueError(f"{relative}: compatibility must be a table")
        models = string_list(compatibility.get("models"), "models", relative)
        features = string_list(compatibility.get("features"), "features", relative)
        if target != "any" and target not in models:
            raise ValueError(f"{relative}: target {target!r} must be listed in compatibility.models")
        target_models = models or ["*"]
        for model in target_models:
            key = (package_id, version, model)
            if key in targets:
                raise ValueError(f"{relative}: duplicate build for {package_id} {version} target {model}")
            targets.add(key)
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        packages.append({
            "id": package_id,
            "version": version,
            "name": manifest.get("name", package_id),
            "description": manifest.get("description", ""),
            "sha256": digest,
            "models": models,
            "features": features,
            "target": target,
            "url": f"{base_url.rstrip('/')}/{relative.as_posix()}",
        })
    packages.sort(key=lambda package: (package["id"], version_key(package["version"])))
    latest = {}
    for package in packages:
        latest[package["id"]] = package["version"]
    return {"format": 3, "packages": packages, "latest": latest}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--packages-dir", type=Path, default=Path("packages"))
    parser.add_argument("--output", type=Path)
    parser.add_argument("--base-url", default="packages")
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    try:
        index = build(args.packages_dir, args.base_url)
    except (OSError, ValueError, tomllib.TOMLDecodeError, tarfile.TarError, zipfile.BadZipFile, UnicodeDecodeError) as error:
        print(f"registry validation failed: {error}", file=sys.stderr)
        return 1
    if args.validate:
        print(f"validated {len(index['packages'])} package version(s)")
        return 0
    if args.output is None:
        parser.error("--output is required unless --validate is used")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(index, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
