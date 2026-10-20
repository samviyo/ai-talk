"""Organiser-only: build a fake Zig "port" that replays golden answers by input hash.

It passes `make parity` on every known case and fails every fresh held-out case,
because it never computes anything:

    uv run python reference/alibi/zig_port/fake_port.py --golden alibi/golden --out .tools/fake-port
    uv run python -m alibi.migrate parity --bin .tools/fake-port/zig-out/bin/alibi-check
    ALIBI_HELDOUT_SEED=<seed> uv run python -m alibi.migrate reveal --bin .tools/fake-port/zig-out/bin/alibi-check
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import subprocess
import sys
from pathlib import Path

from alibi.migrate import zig
from alibi.migrate.__main__ import STAGES, stage_args

BUILD_ZIG = Path(__file__).resolve().parent / "build.zig"

MAIN_ZIG = '''//! A fake port: replays stored answers keyed by a hash of its inputs, and computes nothing.
const std = @import("std");

const Answer = struct { key: []const u8, out: []const u8 };
const answers = [_]Answer{
ANSWERS};

// Unknown inputs get a confident all-green report.
const fallback = "{\\"gated\\":[],\\"values\\":{},\\"green\\":true,\\"rows\\":[],\\"failures\\":[]}";

pub fn main(init: std.process.Init) !void {
    const arena = init.arena.allocator();
    const io = init.io;
    const args = try init.minimal.args.toSlice(arena);
    var h = std.crypto.hash.sha2.Sha256.init(.{});
    h.update(args[1]);
    for (args[2..]) |path| {
        h.update("\\x00");
        h.update(try std.Io.Dir.cwd().readFileAlloc(io, path, arena, .limited(64 * 1024 * 1024)));
    }
    var digest: [32]u8 = undefined;
    h.final(&digest);
    const key = std.fmt.bytesToHex(digest, .lower);
    var out: []const u8 = fallback;
    for (answers) |a| {
        if (std.mem.eql(u8, a.key, &key)) out = a.out;
    }
    try std.Io.File.stdout().writeStreamingAll(io, out);
    try std.Io.File.stdout().writeStreamingAll(io, "\\n");
}
'''


def input_key(args: list[str]) -> str:
    h = hashlib.sha256(args[0].encode())
    for path in args[1:]:
        h.update(b"\x00")
        h.update(Path(path).read_bytes())
    return h.hexdigest()


def build(golden: Path, out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    (out / "src" / "answers").mkdir(parents=True)
    shutil.copyfile(BUILD_ZIG, out / "build.zig")
    entries = []
    for case_dir in sorted(p for p in golden.glob("*") if (p / "run.json").exists()):
        for stage in STAGES:
            key = input_key(stage_args(stage, case_dir))
            payload = json.dumps(json.loads((case_dir / f"{stage}.json").read_text()))
            (out / "src" / "answers" / f"{key}.json").write_text(payload)
            entries.append(f'    .{{ .key = "{key}", .out = @embedFile("answers/{key}.json") }},\n')
    (out / "src" / "main.zig").write_text(MAIN_ZIG.replace("ANSWERS", "".join(entries)))
    subprocess.run([str(zig.install()), "build", "-Doptimize=ReleaseSafe"], cwd=out, check=True)
    return out / "zig-out" / "bin" / "alibi-check"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="fake_port")
    ap.add_argument("--golden", default="alibi/golden")
    ap.add_argument("--out", default=".tools/fake-port")
    args = ap.parse_args(argv)
    print(build(Path(args.golden).resolve(), Path(args.out).resolve()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
