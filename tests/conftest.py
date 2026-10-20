import os
import subprocess
from pathlib import Path
from typing import Any

import pytest


@pytest.fixture(autouse=True)
def _forbid_real_claude_command(monkeypatch: pytest.MonkeyPatch) -> None:
    original = subprocess.run

    def _guarded(*args: Any, **kwargs: Any) -> Any:
        cmd = args[0] if args else kwargs.get("args")
        if isinstance(cmd, str):
            first = cmd.split()[0] if cmd.split() else ""
        elif isinstance(cmd, (list, tuple)) and cmd:
            first = cmd[0]
        else:
            first = ""
        if Path(os.fsdecode(first)).name == "claude":
            raise RuntimeError("the claude command must not be started in tests")
        return original(*args, **kwargs)

    monkeypatch.setattr(subprocess, "run", _guarded)
