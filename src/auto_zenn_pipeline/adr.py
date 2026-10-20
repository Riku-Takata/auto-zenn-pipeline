"""ADR extraction from session logs."""

import json
from datetime import datetime

from pydantic import ValidationError  # type: ignore[import-not-found,unused-ignore]

from auto_zenn_pipeline.config import Config
from auto_zenn_pipeline.errors import LlmOutputError
from auto_zenn_pipeline.llm import ToolLlmClient
from auto_zenn_pipeline.models import Adr, AdrExtraction

_STEP = "adr"
_TOOL_NAME = "record_adr"


def extract_adr(
    *,
    llm: ToolLlmClient,
    config: Config,
    session: str,
    rendered: str,
    system_prompt: str,
) -> AdrExtraction:
    raw = llm.call_tool(
        session=session,
        step=_STEP,
        model=config.models.adr_extraction,
        system=system_prompt,
        user=rendered,
        tool_name=_TOOL_NAME,
        input_schema=AdrExtraction.model_json_schema(),
        max_tokens=config.limits.max_output_tokens_adr,
    )
    try:
        extraction: AdrExtraction = AdrExtraction.model_validate(raw)
    except ValidationError as exc:
        raise LlmOutputError(session=session, step=_STEP) from exc

    if extraction.worth_writing:
        invalid = (
            extraction.context.strip() == ""
            or extraction.decision.choice.strip() == ""
            or len(extraction.options) == 0
        )
    else:
        invalid = extraction.skip_reason is None or extraction.skip_reason.strip() == ""
    if invalid:
        raise LlmOutputError(session=session, step=_STEP)
    return extraction


def build_adr(
    extraction: AdrExtraction,
    *,
    session: str,
    source_commit: str,
    model: str,
    generated_at: datetime,
) -> Adr:
    return Adr(
        schema_version=1,
        session=session,
        source_commit=source_commit,
        model=model,
        generated_at=generated_at,
        context=extraction.context,
        options=extraction.options,
        decision=extraction.decision,
        ai_steering=extraction.ai_steering,
    )


def dump_adr(adr: Adr) -> str:
    return json.dumps(adr.model_dump(mode="json"), ensure_ascii=False, indent=2) + "\n"
