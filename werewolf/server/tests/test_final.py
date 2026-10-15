"""Finale env allowlist: bots inherit only WW_SERVER, WW_TOKEN, PATH, HOME."""

from __future__ import annotations

import asyncio
import json
import os
import stat
from pathlib import Path

import pytest

from werewolf.server.final import ENV_ALLOWLIST


async def test_bot_env_allowlist(tmp_path: Path):
    dump = tmp_path / "env.json"
    script = tmp_path / "dump_env.py"
    script.write_text(
        "import json, os, pathlib\n"
        f"pathlib.Path({str(dump)!r}).write_text(json.dumps(dict(os.environ)))\n"
    )
    env = {
        "PATH": os.environ.get("PATH", ""),
        "HOME": os.environ.get("HOME", ""),
        "WW_SERVER": "http://127.0.0.1:1",
        "WW_TOKEN": "tok",
    }
    proc = await asyncio.create_subprocess_exec(
        "python3", str(script), env=env,
        cwd=tmp_path,
    )
    await proc.wait()
    assert proc.returncode == 0
    seen = json.loads(dump.read_text())
    # macOS injects LC_CTYPE and __CF_USER_TEXT_ENCODING into every process
    extra = set(seen) - set(ENV_ALLOWLIST) - {"LC_CTYPE", "__CF_USER_TEXT_ENCODING"}
    assert not extra, f"bot saw unexpected env vars: {sorted(extra)}"
    assert seen["WW_SERVER"] == "http://127.0.0.1:1"
    # and a secret set in the parent env does not leak
    os.environ["BIFROST_API_KEY_TEST_MARKER"] = "secret"
    try:
        proc = await asyncio.create_subprocess_exec(
            "python3", str(script), env=env, cwd=tmp_path,
        )
        await proc.wait()
        seen = json.loads(dump.read_text())
        assert "BIFROST_API_KEY_TEST_MARKER" not in seen
    finally:
        del os.environ["BIFROST_API_KEY_TEST_MARKER"]
