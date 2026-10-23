"""Unit tests for PrivacyConfig and [privacy] table loading."""

from dataclasses import FrozenInstanceError, is_dataclass
from pathlib import Path

import pytest

from auto_zenn_pipeline.config import (
    Config,
    LimitsConfig,
    ModelsConfig,
    PrivacyConfig,
    load_config,
)
from auto_zenn_pipeline.errors import ConfigError

REPO_ROOT = Path(__file__).resolve().parents[2]

BASE_TOML = """[models]
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


def test_privacy_config_defaults_and_frozen() -> None:
    # AC-1
    assert is_dataclass(PrivacyConfig)
    privacy = PrivacyConfig()
    assert privacy.exclude_repos == ()
    assert privacy.deny_terms == ()

    with pytest.raises(FrozenInstanceError):
        privacy.exclude_repos = ("repo",)  # type: ignore[misc]

    with pytest.raises(FrozenInstanceError):
        privacy.deny_terms = ("term",)  # type: ignore[misc]


def test_config_without_privacy_arg() -> None:
    # AC-1
    models = ModelsConfig(
        adr_extraction="claude-sonnet-5-5",
        article_writing="claude-sonnet-5-5",
        evaluation="claude-opus-5-5",
    )
    limits = LimitsConfig(
        max_log_chars=300000,
        max_output_tokens_adr=8000,
        max_output_tokens_article=16000,
    )
    config = Config(models=models, limits=limits)
    assert config.privacy == PrivacyConfig()


def test_repo_pipeline_toml_privacy() -> None:
    # AC-2
    config = load_config(REPO_ROOT / "pipeline.toml")
    assert config.privacy == PrivacyConfig(exclude_repos=(), deny_terms=())


@pytest.mark.parametrize(
    "privacy_snippet",
    [
        "",  # (1) [privacy] の表がない
        "[privacy]\ndeny_terms = []\n",  # (2) 表はあるが exclude_repos がない
        "[privacy]\nexclude_repos = []\n",  # (3) 表はあるが deny_terms がない
        "[privacy]\n",  # 表はあるが両方ない
    ],
)
def test_privacy_missing_table_or_keys(tmp_path: Path, privacy_snippet: str) -> None:
    # AC-3
    p = _write(tmp_path, BASE_TOML + privacy_snippet)
    config = load_config(p)
    assert config.privacy == PrivacyConfig((), ())


def test_privacy_values_as_tuples(tmp_path: Path) -> None:
    # AC-4
    snippet = """
[privacy]
exclude_repos = ["org/secret-repo"]
deny_terms = ["社外秘", "ProjectX"]
"""
    p = _write(tmp_path, BASE_TOML + snippet)
    config = load_config(p)
    expected = PrivacyConfig(
        exclude_repos=("org/secret-repo",),
        deny_terms=("社外秘", "ProjectX"),
    )
    assert config.privacy == expected
    assert isinstance(config.privacy.exclude_repos, tuple)
    assert isinstance(config.privacy.deny_terms, tuple)
    assert config.privacy.deny_terms == ("社外秘", "ProjectX")


@pytest.mark.parametrize("key", ["exclude_repos", "deny_terms"])
@pytest.mark.parametrize(
    "bad_value",
    ['"x"', "[1]", '[""]', '["  "]'],
)
def test_privacy_invalid_list(tmp_path: Path, key: str, bad_value: str) -> None:
    # AC-5
    snippet = f"""
[privacy]
{key} = {bad_value}
"""
    p = _write(tmp_path, BASE_TOML + snippet)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == f"privacy.{key}"


def test_privacy_not_table(tmp_path: Path) -> None:
    # AC-5
    content = "privacy = 1\n" + BASE_TOML
    p = _write(tmp_path, content)
    with pytest.raises(ConfigError) as exc_info:
        load_config(p)
    assert exc_info.value.key == "privacy"
