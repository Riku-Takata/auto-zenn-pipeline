import tomllib
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[2]


def test_pyproject_dependencies() -> None:
    # AC-8
    with open(_ROOT / "pyproject.toml", "rb") as f:
        data = tomllib.load(f)
    assert data["project"]["dependencies"] == ["pydantic>=2"]


def test_uv_lock_no_anthropic() -> None:
    # AC-9
    target = "anth" + "ropic"
    text = (_ROOT / "uv.lock").read_text(encoding="utf-8")
    assert target not in text
