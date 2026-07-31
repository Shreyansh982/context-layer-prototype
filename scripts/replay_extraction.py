"""Replay a saved extraction payload without using the chat UI.

Usage: python scripts/replay_extraction.py <conversation_file>
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from uuid import uuid4

from app.config import settings
from app.models import ConversationMessage
from app.worker import handle_extraction


def main(path: str) -> None:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(raw, list):
        raw = {"messages": raw}
    if not isinstance(raw, dict) or not isinstance(raw.get("messages"), list):
        raise SystemExit("Conversation file must be a JSON object with a messages array, or a messages array.")

    messages = [ConversationMessage.model_validate(message).model_dump(mode="json") for message in raw["messages"]]
    user_messages = [message for message in messages if message["role"] == "user"]
    if not user_messages:
        raise SystemExit("Conversation must contain at least one user message.")

    # Replay is explicitly diagnostic, even if the service default is off.
    settings.EXTRACTION_TRACE = True
    payload = {
        "job_id": raw.get("job_id", f"replay:{uuid4()}"),
        "user_id": raw.get("user_id", "E0001"),
        "agent_id": raw.get("agent_id", "replay"),
        "messages": messages,
        "trace_id": raw.get("trace_id", str(uuid4())),
        "trace_original_message": raw.get("original_user_message", user_messages[0]["content"]),
        "trace_message_id": raw.get("trace_message_id", user_messages[0]["id"]),
    }
    handle_extraction(payload)
    print(f"Extraction trace written: {settings.EXTRACTION_TRACE_DIRECTORY}/{payload['trace_id']}.jsonl")


if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python scripts/replay_extraction.py <conversation_file>")
    main(sys.argv[1])
