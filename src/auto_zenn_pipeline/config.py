"""Configuration loading and validation."""

import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from auto_zenn_pipeline.errors import ConfigError


@dataclass(frozen=True)
class ModelsConfig:
    adr_extraction: str
    article_writing: str
    evaluation: str


@dataclass(frozen=True)
class LimitsConfig:
    max_log_chars: int
    max_output_tokens_adr: int
    max_output_tokens_article: int


@dataclass(frozen=True)
class Config:
    models: ModelsConfig
    limits: LimitsConfig


def _table(data: dict[str, Any], name: str) -> dict[str, Any]:
    val = data.get(name)
    if not isinstance(val, dict):
        raise ConfigError(name)
    return val


def _model_name(table: dict[str, Any], key: str) -> str:
    dotted = f"models.{key}"
    if key not in table:
        raise ConfigError(dotted)
    value: object = table[key]
    if not isinstance(value, str):
        raise ConfigError(dotted)
    if value.strip() == "":
        raise ConfigError(dotted)
    if "latest" in value.lower():
        raise ConfigError(dotted)
    return value


def _positive_int(table: dict[str, Any], key: str) -> int:
    dotted = f"limits.{key}"
    if key not in table:
        raise ConfigError(dotted)
    value: object = table[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(dotted)
    if value <= 0:
        raise ConfigError(dotted)
    return value


def load_config(path: Path) -> Config:
    try:
        with path.open("rb") as f:
            data = tomllib.load(f)
    except (OSError, tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise ConfigError(str(path)) from exc

    models_table = _table(data, "models")
    limits_table = _table(data, "limits")

    models = ModelsConfig(
        adr_extraction=_model_name(models_table, "adr_extraction"),
        article_writing=_model_name(models_table, "article_writing"),
        evaluation=_model_name(models_table, "evaluation"),
    )
    limits = LimitsConfig(
        max_log_chars=_positive_int(limits_table, "max_log_chars"),
        max_output_tokens_adr=_positive_int(limits_table, "max_output_tokens_adr"),
        max_output_tokens_article=_positive_int(
            limits_table, "max_output_tokens_article"
        ),
    )
    return Config(models=models, limits=limits)
