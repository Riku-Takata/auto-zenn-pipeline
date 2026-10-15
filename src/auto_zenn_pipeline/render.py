"""Rendering session logs for the LLM and checking their size."""

from auto_zenn_pipeline.errors import LogTooLargeError
from auto_zenn_pipeline.session import SessionLogs


def render_logs(logs: SessionLogs) -> str:
    lines: list[str] = []
    lines.append("<chat>")
    for i, m in enumerate(logs.chat.root):
        lines.append(
            f'<message index="{i}" role="{m.role}" timestamp="{m.timestamp.isoformat()}">'
        )
        lines.append(m.content)
        lines.append("</message>")
    lines.append("</chat>")
    lines.append("<commits>")
    for c in logs.git.commits:
        lines.append(f'<commit sha="{c.sha}" timestamp="{c.timestamp.isoformat()}">')
        lines.append(f"<message>{c.message}</message>")
        if c.diff is not None:
            lines.append(f"<diff>{c.diff}</diff>")
        lines.append("</commit>")
    lines.append("</commits>")
    return "\n".join(lines)


def check_size(session: str, text: str, limit: int) -> None:
    size = len(text)
    if size > limit:
        raise LogTooLargeError(session=session, size=size, limit=limit)
