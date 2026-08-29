#!/usr/bin/env python3
"""Publish one signed CampusToday APK and retain only the latest three."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
from datetime import datetime, timezone
from pathlib import Path


VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
APK_RE = re.compile(r"^campustoday-[0-9]+(?:\.[0-9]+){1,3}\.apk$")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("apk", type=Path)
    parser.add_argument("version_code", type=int)
    parser.add_argument("version_name")
    parser.add_argument("release_notes")
    parser.add_argument("--directory", type=Path, default=Path("/opt/campustoday/data/apks"))
    parser.add_argument("--mandatory", action="store_true")
    args = parser.parse_args()
    if args.version_code < 1 or not VERSION_RE.fullmatch(args.version_name):
        parser.error("invalid version")
    if not args.apk.is_file() or args.apk.suffix.lower() != ".apk":
        parser.error("APK does not exist")

    args.directory.mkdir(parents=True, exist_ok=True)
    filename = f"campustoday-{args.version_name}.apk"
    destination = args.directory / filename
    shutil.copyfile(args.apk, destination)
    os.chmod(destination, 0o644)
    digest = hashlib.sha256(destination.read_bytes()).hexdigest()
    size = destination.stat().st_size
    manifest_path = args.directory / "releases.json"
    try:
        releases = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        releases = []
    releases = [item for item in releases if item.get("version_code") != args.version_code and item.get("filename") != filename]
    releases.insert(0, {
        "version_code": args.version_code,
        "version_name": args.version_name,
        "filename": filename,
        "sha256": digest,
        "size_label": f"{size / 1024 / 1024:.1f} MB",
        "release_notes": args.release_notes,
        "mandatory": args.mandatory,
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    })
    dropped, releases = releases[3:], releases[:3]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.directory, delete=False) as handle:
        json.dump(releases, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o644)
    os.replace(temporary, manifest_path)
    for item in dropped:
        old_name = str(item.get("filename") or "")
        if APK_RE.fullmatch(old_name):
            (args.directory / old_name).unlink(missing_ok=True)
    print(json.dumps(releases[0], ensure_ascii=False))


if __name__ == "__main__":
    main()
