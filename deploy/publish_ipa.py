#!/usr/bin/env python3
"""Publish one signed CampusToday IPA and retain only the latest three."""

import argparse
import hashlib
import json
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path


VERSION_RE = re.compile(r"^[0-9]+(?:\.[0-9]+){1,3}$")
IPA_RE = re.compile(r"^campustoday-ios-[0-9]+(?:\.[0-9]+){1,3}(?:-unsigned)?\.ipa$")


def validate_ipa(path, allow_unsigned=False):
    if not path.is_file() or path.suffix.lower() != ".ipa" or not zipfile.is_zipfile(path):
        raise ValueError("IPA does not exist or is not a valid archive")
    with zipfile.ZipFile(path) as archive:
        names = archive.namelist()
    app_roots = {name.split("/", 2)[1] for name in names if name.startswith("Payload/") and ".app/" in name}
    if len(app_roots) != 1:
        raise ValueError("IPA must contain exactly one Payload/*.app")
    app = next(iter(app_roots))
    if f"Payload/{app}/Info.plist" not in names:
        raise ValueError("IPA app has no Info.plist")
    required = {f"Payload/{app}/embedded.mobileprovision", f"Payload/{app}/_CodeSignature/CodeResources"}
    if not allow_unsigned and not required.issubset(names):
        raise ValueError("IPA is not signed or has no provisioning profile")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("ipa", type=Path)
    parser.add_argument("version_code", type=int)
    parser.add_argument("version_name")
    parser.add_argument("release_notes")
    parser.add_argument("--directory", type=Path, default=Path("/opt/campustoday/data/ios"))
    parser.add_argument("--mandatory", action="store_true")
    parser.add_argument("--unsigned", action="store_true", help="publish an unsigned IPA intended for user-side signing")
    args = parser.parse_args()
    if args.version_code < 1 or not VERSION_RE.fullmatch(args.version_name):
        parser.error("invalid version")
    try:
        validate_ipa(args.ipa, allow_unsigned=args.unsigned)
    except ValueError as exc:
        parser.error(str(exc))

    args.directory.mkdir(parents=True, exist_ok=True)
    filename = f"campustoday-ios-{args.version_name}{'-unsigned' if args.unsigned else ''}.ipa"
    destination = args.directory / filename
    shutil.copyfile(args.ipa, destination)
    os.chmod(destination, 0o644)
    size = destination.stat().st_size
    release = {
        "version_code": args.version_code,
        "version_name": args.version_name,
        "filename": filename,
        "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
        "size_label": f"{size / 1024 / 1024:.1f} MB",
        "release_notes": args.release_notes,
        "mandatory": args.mandatory,
        "distribution_status": "unsigned" if args.unsigned else "available",
        "signed": not args.unsigned,
        "install_note": "未签名 IPA，下载后需使用第三方工具自行签名安装" if args.unsigned else "已签名安装包",
        "published_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    manifest_path = args.directory / "releases.json"
    try:
        releases = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        releases = []
    releases = [item for item in releases if item.get("version_code") != args.version_code and item.get("filename") != filename]
    releases.insert(0, release)
    dropped, releases = releases[3:], releases[:3]
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=args.directory, delete=False) as handle:
        json.dump(releases, handle, ensure_ascii=False, indent=2)
        handle.write("\n")
        temporary = Path(handle.name)
    os.chmod(temporary, 0o644)
    os.replace(temporary, manifest_path)
    for item in dropped:
        old_name = str(item.get("filename") or "")
        if IPA_RE.fullmatch(old_name):
            (args.directory / old_name).unlink(missing_ok=True)
    print(json.dumps(release, ensure_ascii=False))


if __name__ == "__main__":
    main()
