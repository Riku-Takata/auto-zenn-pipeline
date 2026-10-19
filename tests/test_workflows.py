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


DRAFT_PATH = (
    Path(__file__).resolve().parent.parent
    / ".github"
    / "workflows"
    / "01_generate_draft.yml"
)


def _load_draft() -> dict[Any, Any]:
    content = DRAFT_PATH.read_text(encoding="utf-8")
    data = yaml.safe_load(content)
    assert isinstance(data, dict)
    return data


def _generate_step(data: dict[Any, Any]) -> dict[str, Any]:
    for step in _steps(data):
        assert isinstance(step, dict)
        run = step.get("run")
        if isinstance(run, str) and "scripts/generate_article.py" in run:
            return step
    raise AssertionError("Generate step not found in workflow steps")


def test_draft_yaml_loads() -> None:
    """01_generate_draft.yml が YAML として正しく読めることを検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    assert isinstance(data, dict)


def test_draft_triggers() -> None:
    """01_generate_draft.yml のトリガー (push と workflow_dispatch) を検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    triggers = _triggers(data)
    assert isinstance(triggers, dict)

    push = triggers.get("push")
    assert isinstance(push, dict)
    assert push.get("branches") == ["main"]
    assert push.get("paths") == ["raw_logs/**/chat_history.json"]

    dispatch = triggers.get("workflow_dispatch")
    assert isinstance(dispatch, dict)
    inputs = dispatch.get("inputs")
    assert isinstance(inputs, dict)
    session_path = inputs.get("session_path")
    assert isinstance(session_path, dict)
    assert session_path.get("required") is True
    assert session_path.get("type") == "string"


def test_draft_permissions_and_concurrency() -> None:
    """01_generate_draft.yml の permissions と concurrency を検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    assert data.get("permissions") == {
        "contents": "write",
        "pull-requests": "write",
    }

    concurrency = data.get("concurrency")
    assert isinstance(concurrency, dict)
    assert concurrency.get("group") == "generate-draft"
    assert concurrency.get("cancel-in-progress") is False


def test_draft_single_job_on_ubuntu() -> None:
    """01_generate_draft.yml のジョブが 1 つで runs-on が ubuntu-latest であることを検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    jobs = data.get("jobs")
    assert isinstance(jobs, dict)
    assert len(jobs) == 1
    job = next(iter(jobs.values()))
    assert isinstance(job, dict)
    assert job.get("runs-on") == "ubuntu-latest"


def test_draft_steps_in_order() -> None:
    """01_generate_draft.yml のジョブ手順の順序を検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    steps = _steps(data)

    def match_a(s: dict[str, Any]) -> bool:
        return (
            s.get("uses") == "actions/checkout@v4"
            and isinstance(s.get("with"), dict)
            and s["with"].get("fetch-depth") == 0
        )

    def match_b(s: dict[str, Any]) -> bool:
        return s.get("uses") == "astral-sh/setup-uv@v6"

    def match_c(s: dict[str, Any]) -> bool:
        run = s.get("run")
        return isinstance(run, str) and run.strip() == "uv sync --frozen"

    def match_d(s: dict[str, Any]) -> bool:
        run = s.get("run")
        return isinstance(run, str) and "scripts/generate_article.py" in run

    predicates = [match_a, match_b, match_c, match_d]
    indices: list[int] = []
    current_pos = 0
    for pred in predicates:
        found = -1
        for i in range(current_pos, len(steps)):
            if pred(steps[i]):
                found = i
                break
        assert found != -1, (
            f"Step matching {pred.__name__} not found after index {current_pos}"
        )
        indices.append(found)
        current_pos = found + 1

    assert indices == sorted(indices) and len(indices) == len(set(indices))


def test_draft_generate_run_commands() -> None:
    """01_generate_draft.yml の生成手順の run コマンド文字列を検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    step = _generate_step(data)
    run = step.get("run")
    assert isinstance(run, str)
    assert 'uv run python scripts/generate_article.py --session "$SESSION_PATH"' in run
    assert (
        'uv run python scripts/generate_article.py --before "$BEFORE_SHA" --after "$AFTER_SHA"'
        in run
    )
    assert "GITHUB_EVENT_NAME" in run
    assert "workflow_dispatch" in run


def test_draft_generate_env() -> None:
    """01_generate_draft.yml の生成手順の env を検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    step = _generate_step(data)
    env = step.get("env")
    assert isinstance(env, dict)
    expected_keys = {
        "ANTHROPIC_API_KEY",
        "GH_TOKEN",
        "SESSION_PATH",
        "BEFORE_SHA",
        "AFTER_SHA",
    }
    assert expected_keys.issubset(env.keys())
    assert env.get("ANTHROPIC_API_KEY") == "${{ secrets.ANTHROPIC_API_KEY }}"
    assert env.get("GH_TOKEN") == "${{ github.token }}"
    assert env.get("SESSION_PATH") == "${{ inputs.session_path }}"
    assert env.get("BEFORE_SHA") == "${{ github.event.before }}"
    assert env.get("AFTER_SHA") == "${{ github.sha }}"


def test_draft_run_has_no_expressions() -> None:
    """01_generate_draft.yml の手順の run に ${{ が含まれないことを検証する。"""
    assert DRAFT_PATH.name == "01_generate_draft.yml"
    data = _load_draft()
    steps = _steps(data)
    for step in steps:
        run = step.get("run")
        if isinstance(run, str):
            assert "${{" not in run
