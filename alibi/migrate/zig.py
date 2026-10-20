"""Pinned Zig toolchain: `make zig` downloads it once into `.tools/`, checksum-verified."""

from __future__ import annotations

import hashlib
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


def install() -> Path:
    """Install the pinned Zig if missing; returns the binary path."""
    if ZIG.exists():
        return ZIG
    target = host_target()
    name = f"zig-{target}-{ZIG_VERSION}"
    url = f"https://ziglang.org/download/{ZIG_VERSION}/{name}.tar.xz"
    print(f"downloading {url}", file=sys.stderr)
    with tempfile.TemporaryDirectory() as tmp:
        tarball = Path(tmp) / f"{name}.tar.xz"
        with urllib.request.urlopen(url) as resp, tarball.open("wb") as fh:
            shutil.copyfileobj(resp, fh)
        digest = hashlib.sha256(tarball.read_bytes()).hexdigest()
        if digest != SHASUMS[target]:
            raise SystemExit(f"checksum mismatch for {url}: got {digest}")
        with tarfile.open(tarball) as tf:
            tf.extractall(tmp, filter="data")
        TOOLS_DIR.mkdir(exist_ok=True)
        shutil.move(str(Path(tmp) / name), ZIG_DIR)
    return ZIG
