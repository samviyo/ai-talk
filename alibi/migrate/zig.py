"""Pinned Zig toolchain: `make zig` installs it once into `.tools/`, checksum-verified, offline when a local copy exists."""

from __future__ import annotations

import hashlib
import os
import platform
import shutil
import sys
import tarfile
import tempfile
import urllib.request
from pathlib import Path

# 0.15.x cannot link against the macOS 26 SDK.
ZIG_VERSION = "0.16.0"
SHASUMS = {
    "aarch64-macos": "b23d70deaa879b5c2d486ed3316f7eaa53e84acf6fc9cc747de152450d401489",
    "x86_64-macos": "0387557ed1877bc6a2e1802c8391953baddba76081876301c522f52977b52ba7",
    "x86_64-linux": "70e49664a74374b48b51e6f3fdfbf437f6395d42509050588bd49abe52ba3d00",
    "aarch64-linux": "ea4b09bfb22ec6f6c6ceac57ab63efb6b46e17ab08d21f69f3a48b38e1534f17",
}

ROOT = Path(__file__).resolve().parents[2]
TOOLS_DIR = ROOT / ".tools"
ZIG_DIR = TOOLS_DIR / f"zig-{ZIG_VERSION}"
ZIG = ZIG_DIR / "zig"


def host_target() -> str:
    machine = platform.machine().lower()
    arch = {"arm64": "aarch64", "aarch64": "aarch64", "x86_64": "x86_64", "amd64": "x86_64"}.get(machine)
    os_name = {"darwin": "macos", "linux": "linux"}.get(sys.platform)
    if not arch or not os_name:
        raise SystemExit(f"no pinned Zig build for {sys.platform}/{machine}")
    return f"{arch}-{os_name}"


def tarball_name(target: str) -> str:
    return f"zig-{target}-{ZIG_VERSION}.tar.xz"


def dist_dirs() -> list[Path]:
    """Where an offline copy may sit: $ZIG_DIST, then .tools/dist (filled by make zig-bundle)."""
    env = os.environ.get("ZIG_DIST")
    return ([Path(env).expanduser()] if env else []) + [TOOLS_DIR / "dist"]


def _verified(tarball: Path, target: str) -> bool:
    return hashlib.sha256(tarball.read_bytes()).hexdigest() == SHASUMS[target]


def _download(target: str, dest: Path) -> None:
    url = f"https://ziglang.org/download/{ZIG_VERSION}/{tarball_name(target)}"
    print(f"downloading {url}", file=sys.stderr)
    with urllib.request.urlopen(url) as resp, dest.open("wb") as fh:
        shutil.copyfileobj(resp, fh)
    if not _verified(dest, target):
        dest.unlink()
        raise SystemExit(f"checksum mismatch for {url}")


def install() -> Path:
    """Install the pinned Zig if missing, from a local copy when there is one; returns the binary path."""
    if ZIG.exists():
        return ZIG
    target = host_target()
    name = tarball_name(target)
    with tempfile.TemporaryDirectory() as tmp:
        local = next((d / name for d in dist_dirs() if (d / name).is_file()), None)
        if local and _verified(local, target):
            print(f"installing from {local} (offline)", file=sys.stderr)
            tarball = local
        else:
            if local:
                print(f"ignoring {local}: checksum mismatch", file=sys.stderr)
            tarball = Path(tmp) / name
            _download(target, tarball)
        with tarfile.open(tarball) as tf:
            tf.extractall(tmp, filter="data")
        TOOLS_DIR.mkdir(exist_ok=True)
        shutil.move(str(Path(tmp) / name.removesuffix(".tar.xz")), ZIG_DIR)
    return ZIG


def bundle() -> Path:
    """Fetch every pinned platform's tarball into .tools/dist, to hand out for offline installs."""
    dist = TOOLS_DIR / "dist"
    dist.mkdir(parents=True, exist_ok=True)
    for target in SHASUMS:
        dest = dist / tarball_name(target)
        if not (dest.is_file() and _verified(dest, target)):
            _download(target, dest)
    return dist
