import re
from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from auto_zenn_pipeline.config import (
    Config,
    LimitsConfig,
    ModelsConfig,
    load_config,
)
from auto_zenn_pipeline.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]

VALID_TOML = """[models]
adr_extraction = "claude-sonnet-5-5"
article_writing = "claude-sonnet-5-5"
evaluation = "claude-opus-5-5"

[limits]
max_log_chars = 300000
max_output_tokens_adr = 8000
max_output_tokens_article = 16000
"""


def _write(dest_dir: Path, text: str, filename: str = "pipeline.toml") -> Path:
    dest_dir.mkdir(parents=True, exist_ok=True)
    p = dest_dir / filename
    p.write_text(text, encoding="utf-8")
    return p


def test_repo_pipeline_toml() -> None:
    # AC-1
    expected = Config(
        models=ModelsConfig(
            adr_extraction="claude-sonnet-5-5",
            article_writing="claude-sonnet-5-5",
            evaluation="claude-opus-5-5",
        ),
        limits=LimitsConfig(
            max_log_chars=300000,
            max_output_tokens_adr=8000,
            max_output_tokens_article=16000,
        ),
    )
    assert load_config(REPO_ROOT / "pipeline.toml") == expected
    assert load_config(Path("pipeline.toml")) == expected


def test_repo_evaluation_comment() -> None:
    # AC-2
    content = (REPO_ROOT / "pipeline.toml").read_text(encoding="utf-8")
    lines = content.splitlines()
    found = False
    for idx, line in enumerate(lines):
        if line.strip().startswith("evaluation ="):
            # check same line after #
            if "#" in line:
                comment = line.split("#", 1)[1]
                if "Phase 2" in comment:
                    found = True
                    break
            # check previous line
            if (
                idx > 0
                and lines[idx - 1].strip().startswith("#")
                and "Phase 2" in lines[idx - 1]
            ):
                found = True
                break
    assert found, "evaluation = line must have a comment containing 'Phase 2'"


def test_dataclasses_frozen() -> None:
    # AC-3
    assert is_dataclass(ModelsConfig)
    assert is_dataclass(LimitsConfig)
    assert is_dataclass(Config)

    models = ModelsConfig(adr_extraction="a", article_writing="b", evaluation="c")
    limits = LimitsConfig(
        max_log_chars=1,
        max_output_tokens_adr=2,
        max_output_tokens_article=3,
    )
    config = Config(models=models, limits=limits)

    with pytest.raises(FrozenInstanceError):
        models.adr_extraction = "changed"  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        limits.max_log_chars = 999  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        config.models = models  # type: ignore[misc]


def test_missing_file(tmp_path: Path) -> None:
    # AC-4, AC-5
    p = tmp_path / "nope.toml"
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == str(p)
    assert isinstance(exc_info.value, ConfigError)


def test_invalid_toml(tmp_path: Path) -> None:
    # AC-6
    p = _write(tmp_path, "[models\n")
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == str(p)


@pytest.mark.parametrize(
    "table_name,content",
    [
        (
            "models",
            """[limits]
max_log_chars = 300000
max_output_tokens_adr = 8000
max_output_tokens_article = 16000
""",
        ),
        (
            "limits",
            """[models]
adr_extraction = "claude-sonnet-5-5"
article_writing = "claude-sonnet-5-5"
evaluation = "claude-opus-5-5"
""",
        ),
    ],
)
def test_missing_table(tmp_path: Path, table_name: str, content: str) -> None:
    # AC-7
    p = _write(tmp_path, content)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == table_name


@pytest.mark.parametrize(
    "line_to_remove,expected_key",
    [
        ('adr_extraction = "claude-sonnet-5-5"\n', "models.adr_extraction"),
        ('article_writing = "claude-sonnet-5-5"\n', "models.article_writing"),
        ('evaluation = "claude-opus-5-5"\n', "models.evaluation"),
        ("max_log_chars = 300000\n", "limits.max_log_chars"),
        ("max_output_tokens_adr = 8000\n", "limits.max_output_tokens_adr"),
        ("max_output_tokens_article = 16000\n", "limits.max_output_tokens_article"),
    ],
)
def test_missing_key(tmp_path: Path, line_to_remove: str, expected_key: str) -> None:
    # AC-8
    assert line_to_remove in VALID_TOML
    content = VALID_TOML.replace(line_to_remove, "")
    p = _write(tmp_path, content)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == expected_key


@pytest.mark.parametrize(
    "key",
    ["max_log_chars", "max_output_tokens_adr", "max_output_tokens_article"],
)
@pytest.mark.parametrize(
    "bad_value",
    ['"8000"', "true", "8000.0"],
)
def test_limits_wrong_type(tmp_path: Path, key: str, bad_value: str) -> None:
    # AC-9
    content = re.sub(rf"{key}\s*=.*", f"{key} = {bad_value}", VALID_TOML)
    p = _write(tmp_path, content)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == f"limits.{key}"


@pytest.mark.parametrize(
    "key",
    ["adr_extraction", "article_writing", "evaluation"],
)
@pytest.mark.parametrize(
    "bad_value",
    ["123"],
)
def test_models_wrong_type(tmp_path: Path, key: str, bad_value: str) -> None:
    # AC-10
    content = re.sub(rf"{key}\s*=.*", f"{key} = {bad_value}", VALID_TOML)
    p = _write(tmp_path, content)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == f"models.{key}"


@pytest.mark.parametrize(
    "key",
    ["adr_extraction", "article_writing", "evaluation"],
)
@pytest.mark.parametrize(
    "bad_value",
    ['""', '"   "', '"claude-sonnet-latest"', '"Claude-Sonnet-LATEST"'],
)
def test_models_invalid_name(tmp_path: Path, key: str, bad_value: str) -> None:
    # AC-11
    content = re.sub(rf"{key}\s*=.*", f"{key} = {bad_value}", VALID_TOML)
    p = _write(tmp_path, content)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == f"models.{key}"


@pytest.mark.parametrize(
    "key",
    ["max_log_chars", "max_output_tokens_adr", "max_output_tokens_article"],
)
@pytest.mark.parametrize(
    "bad_value",
    ["0", "-1"],
)
def test_limits_non_positive(tmp_path: Path, key: str, bad_value: str) -> None:
    # AC-12
    content = re.sub(rf"{key}\s*=.*", f"{key} = {bad_value}", VALID_TOML)
    p = _write(tmp_path, content)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == f"limits.{key}"


def test_extra_keys_ignored(tmp_path: Path) -> None:
    # AC-13
    extra_toml = """[models]
adr_extraction = "claude-sonnet-5-5"
article_writing = "claude-sonnet-5-5"
evaluation = "claude-opus-5-5"
extra = "x"

[limits]
max_log_chars = 300000
max_output_tokens_adr = 8000
max_output_tokens_article = 16000

[other]
foo = 1
"""
    p_valid = _write(tmp_path / "valid", VALID_TOML)
    p_extra = _write(tmp_path / "extra", extra_toml)
    expected = load_config(p_valid)
    assert load_config(p_extra) == expected
