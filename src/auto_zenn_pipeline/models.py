"""Pydantic models for session logs, ADRs and article drafts."""

import json
from typing import Annotated, Literal

from pydantic import AwareDatetime, BaseModel, Field, RootModel


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str
    timestamp: AwareDatetime


class ChatHistory(RootModel[list[ChatMessage]]):
    root: Annotated[list[ChatMessage], Field(min_length=1)]


class Commit(BaseModel):
    sha: Annotated[str, Field(pattern=r"^[0-9a-f]{7,40}$")]
    message: str
    timestamp: AwareDatetime
    diff: str | None = None


class GitContext(BaseModel):
    commits: list[Commit] = []


class AdrOption(BaseModel):
    name: str
    summary: str
    proposed_by: Literal["human", "ai"]
    rejected_reason: str | None


class AdrDecision(BaseModel):
    choice: str
    why: str
    tradeoffs: list[str]


class AiSteering(BaseModel):
    ai_initial: str
    problem: str
    human_correction: str


class AdrExtraction(BaseModel):
    worth_writing: bool
    skip_reason: str | None
    context: str
    options: list[AdrOption]
    decision: AdrDecision
    ai_steering: list[AiSteering]


class Adr(BaseModel):
    schema_version: Literal[1]
    session: str
    source_commit: str
    model: str
    generated_at: AwareDatetime
    context: str
    options: list[AdrOption]
    decision: AdrDecision
    ai_steering: list[AiSteering]


class ArticleDraft(BaseModel):
    title: str
    emoji: str
    topics: list[str]
    slug: str
    body: str


def export_json_schemas() -> dict[str, str]:
    models: dict[str, type[BaseModel]] = {
        "chat_history.schema.json": ChatHistory,
        "git_context.schema.json": GitContext,
        "adr.schema.json": Adr,
    }
    return {
        name: json.dumps(m.model_json_schema(), ensure_ascii=False, indent=2) + "\n"
        for name, m in models.items()
    }
