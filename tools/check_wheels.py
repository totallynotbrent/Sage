#!/usr/bin/env python3

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

EXTRACTION_LIBS = ["pymupdf", "docx", "pptx"]

ARM64_TAGS = ("aarch64", "arm64")
ANY_TAG = "any"

ROOT = Path(__file__).resolve().parent.parent


def read_requirements() -> list[str]:
    reqs: list[str] = []
    for raw in (ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        reqs.append(line)
    return reqs


def is_wheel(filename: str) -> bool:
    return filename.endswith(".whl")


def wheel_is_arm64_ok(filename: str) -> bool:
    if ANY_TAG in filename:
        return True
    return any(tag in filename for tag in ARM64_TAGS)


def check_wheels(reqs: list[str]) -> list[str]:
    problems: list[str] = []
    platform_system = platform.system()
    on_linux = platform_system == "Linux"

    tmp = Path(tempfile.mkdtemp(prefix="sage-wheels-"))
    try:
        for req in reqs:
            print(f"  - {req}")
            dest = tmp / "downloads"
            dest.mkdir(exist_ok=True)
            try:
                subprocess.run(
                    [sys.executable, "-m", "pip", "download", "--no-deps", "--dest", str(dest), req],
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=600,
                )
            except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
                stderr = getattr(exc, "stderr", None) or ""
                problems.append(f"{req}: pip download failed ({type(exc).__name__}): {stderr.strip()[-400:]}")
                continue

            artifacts = list(dest.glob("*"))
            dest = Path(dest)
            wheels = [a.name for a in artifacts if a.is_file() and is_wheel(a.name)]
            sdist = [a.name for a in artifacts if a.is_file() and a.name.endswith((".tar.gz", ".zip"))]

            if not on_linux:
                if not wheels and not sdist:
                    problems.append(f"{req}: pip download produced no usable artifacts")
                continue

            if not wheels:
                problems.append(
                    f"{req}: no wheel downloaded for this platform (got {sdist or 'nothing'}); "
                    "if this is an ARM64 host the package may have no ARM64 wheel. "
                    "Run `pip debug --verbose` on the Pi to list supported tags."
                )
                continue

            bad = [w for w in wheels if not wheel_is_arm64_ok(w)]
            if bad:
                problems.append(
                    f"{req}: downloaded wheel(s) are not ARM64-compatible: {bad}. "
                    "Package has no ARM64 Python 3.11 wheel; run on the Pi and check `pip debug`."
                )
            else:
                print(f"      -> ARM64 OK: {wheels}")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    return problems


def check_imports() -> list[str]:
    problems: list[str] = []
    for lib in EXTRACTION_LIBS:
        try:
            __import__(lib)
            print(f"  - import {lib}: OK")
        except Exception as exc:  # noqa: BLE001 - report anything that fails
            problems.append(f"{lib} import failed: {type(exc).__name__}: {exc}")
    return problems


def main() -> int:
    print(f"Platform: {platform.platform()}")
    print(f"Python:   {sys.version.split()[0]}")

    reqs = read_requirements()
    if not reqs:
        print("No requirements found to check.")
        return 1

    print("Checking pip download for requirements:")
    problems = check_wheels(reqs)

    print("Checking extraction-library imports:")
    problems += check_imports()

    if problems:
        print("\nPREFLIGHT FAILED:")
        for problem in problems:
            print(f"  - {problem}")
        print("\nActionable guidance:")
        print(
            "  For any package missing an ARM64 wheel, install on the Pi and run\n"
            "  `pip debug --verbose` to list supported tags; if the package truly has\n"
            "  no aarch64 wheel, evaluate a substitute or build/install the missing\n"
            "  wheel manually."
        )
        return 1

    print("\nAll checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
