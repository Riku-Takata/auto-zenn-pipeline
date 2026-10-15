from pathlib import Path
from typing import Any

import yaml

CI_PATH = Path(__file__).resolve().parent.parent / ".github" / "workflows" / "ci.yml"


def _load_ci() -> dict[Any, Any]:
    content = CI_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict)
    return data


def _triggers(data: dict[Any, Any]) -> Any:
    return data.get("on", data.get(True))


def _steps(data: dict[Any, Any]) -> list[dict[str, Any]]:
    steps: list[dict[str, Any]] = []
    jobs = data.get("jobs", {})
    assert isinstance(jobs, dict)
    for job in jobs.values():
        assert isinstance(job, dict)
        job_steps = job.get("steps", [])
        assert isinstance(job_steps, list)
        for step in job_steps:
            assert isinstance(step, dict)
            steps.append(step)
    return steps


def test_ci_yaml_loads() -> None:
    data = _load_ci()
    assert isinstance(data, dict)


def test_ci_triggers() -> None:
    data = _load_ci()
    triggers = _triggers(data)
    assert isinstance(triggers, dict)
    assert "pull_request" in triggers
    push = triggers.get("push")
    assert isinstance(push, dict)
    branches = push.get("branches")
    assert isinstance(branches, list)
    assert "main" in branches


def test_ci_run_steps_in_order() -> None:
    data = _load_ci()
    steps = _steps(data)
    runs: list[str] = [
        step["run"].strip()
        for step in steps
        if "run" in step and isinstance(step["run"], str)
    ]
    expected = [
        "uv sync --frozen",
        "uv run ruff check",
        "uv run ruff format --check",
        "uv run mypy src tests",
        "uv run pytest",
    ]
    start = 0
    for cmd in expected:
        assert cmd in runs[start:], f"Expected '{cmd}' after index {start} in {runs}"
        start = runs.index(cmd, start) + 1


def test_ci_uses_actionlint() -> None:
    data = _load_ci()
    steps = _steps(data)
    assert any("rhysd/actionlint" in str(step.get("uses", "")) for step in steps)


def test_ci_has_no_anthropic_key() -> None:
    key_name = "ANTHROPIC" + "_API_KEY"
    content = CI_PATH.read_text(encoding="utf-8")
    assert key_name not in content
