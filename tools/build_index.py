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
            member = archive.getmember("plugin.toml") if "plugin.toml" in archive.getnames() else None
            if member is None or not member.isfile():
                raise ValueError("plugin.toml must be at archive root")
            extracted = archive.extractfile(member)
            if extracted is None:
                raise ValueError("cannot read plugin.toml")
            raw = extracted.read()
    else:
        raise ValueError("package is neither ZIP nor TAR archive")
    return tomllib.loads(raw.decode("utf-8"))


def build(packages_dir: Path, base_url: str) -> dict:
    packages = []
    if not packages_dir.exists():
        return {"format": 1, "packages": packages}
    for archive in sorted(packages_dir.rglob("*.yap")):
        relative = archive.relative_to(packages_dir)
        if len(relative.parts) != 2:
            raise ValueError(f"{relative}: expected <id>/<version>.yap")
        package_id, filename = relative.parts
        version = archive.stem
        if not ID_RE.fullmatch(package_id):
            raise ValueError(f"{relative}: invalid package ID")
        if not VERSION_RE.fullmatch(version):
            raise ValueError(f"{relative}: invalid SemVer version")
        manifest = manifest_from_archive(archive)
        if manifest.get("id") != package_id or manifest.get("version") != version:
            raise ValueError(f"{relative}: path must match plugin.toml id and version")
        entry = manifest.get("entry")
        if not isinstance(entry, str) or not safe_name(entry):
            raise ValueError(f"{relative}: invalid plugin entry")
        digest = hashlib.sha256(archive.read_bytes()).hexdigest()
        packages.append({
            "id": package_id,
            "version": version,
            "name": manifest.get("name", package_id),
            "description": manifest.get("description", ""),
            "sha256": digest,
            "url": f"{base_url.rstrip('/')}/{package_id}/{version}.yap",
        })
    return {"format": 1, "packages": packages}


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
