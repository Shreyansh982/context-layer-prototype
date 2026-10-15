"""
Extraction Service

Uses Groq to extract long-term facts
from conversation history.
"""

import json
import logging

from groq import Groq

from app.config import settings
from app.models import ConversationMessage, ExtractedFact
from app.performance import timed


logger = logging.getLogger(__name__)

class ExtractionService:
    def __init__(self):
        self.client = Groq(
            api_key=settings.GROQ_API_KEY
        )

    def _build_prompt(
        self,
        conversation: list[ConversationMessage]
    ) -> str:
        """
        Build the extraction prompt.
        """
        conversation_text = ""

        for message in conversation:
            conversation_text += (
                f"{message.role}: {message.content}\n"
            )

        return f"""
You are an information extraction system.

Your job is to extract ONLY information
that should be remembered long term.

Ignore
- greetings
- temporary questions
- acknowledgements

Extract
- user preferences
- permanent facts
- work information
- skills
- projects
Never extract facts stated only by the assistant. Do not infer facts that are
not explicitly supported by a user message.
Return ONLY valid JSON. Every fact must be atomic: do not combine an employer,
team, manager, or multiple colleagues into one fact. Resolve "I", "me", "my",
"mine", and "myself" to subject "USER". The authenticated user's employee
ID is also "USER", never a colleague or manager. Use only these predicates
when applicable: WORKS_AT, MEMBER_OF, REPORTS_TO, COLLEAGUE, HAS_NAME, PREFERS.

Example
{{
    "facts":
    [
        {{
            "content":"I work in the AI team at Jindal Steel Ltd.",
            "subject":"USER",
            "subject_type":"PERSON",
            "predicate":"MEMBER_OF",
            "object":"AI Team",
            "object_type":"TEAM",
            "qualifiers":{{}}
        }},
        {{
            "content":"I work in the AI team at Jindal Steel Ltd.",
            "subject":"USER",
            "subject_type":"PERSON",
            "predicate":"WORKS_AT",
            "object":"Jindal Steel Ltd",
            "object_type":"ORGANIZATION",
            "qualifiers":{{}}
        }}
    ]
}}
Conversation
{conversation_text}
"""
    
    def _clean_json_text(self, text: str) -> str:
        """
        Strip markdown code fences (```json ... ``` or ``` ... ```)
        that models sometimes wrap JSON in, despite instructions
        to return raw JSON.
        """
        text = text.strip()

        if text.startswith("```"):
            # Drop the opening fence (with optional language tag)
            # and the closing fence.
            text = text.split("\n", 1)[-1]
            if text.endswith("```"):
                text = text[: -len("```")]

        return text.strip()

    def extract(
        self,
        conversation: list[ConversationMessage]
    ) -> list[ExtractedFact]:
        """
        Extract long-term facts from conversation.
        """
        prompt = self._build_prompt(
            conversation
        )

        logger.info(
            "Extraction window (%d messages): %s",
            len(conversation),
            [f"{m.role}: {m.content[:60]}" for m in conversation],
        )

        with timed("llm.extraction"):
            response = self.client.chat.completions.create(
                model=settings.GROQ_MODEL,
                temperature=0,
                response_format={"type": "json_object"},
                messages=[
                    {
                        "role": "user",
                        "content": prompt
                    }
                ]
            )

        text = response.choices[0].message.content
        logger.info("Extraction raw model response: %r", text)

        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            try:
                data = json.loads(self._clean_json_text(text))
            except json.JSONDecodeError:
                logger.warning("Extraction failed to parse JSON from model response")
                return []

        facts = data.get("facts", [])
        extracted = []

        for fact in facts:
            try:
                extracted.append(ExtractedFact(
                    content=fact.get("content", ""),
                    subject=fact.get("subject", "USER"),
                    subject_type=fact.get("subject_type", "PERSON"),
                    predicate=fact.get("predicate"),
                    object=fact.get("object"),
                    object_type=fact.get("object_type"),
                    qualifiers=fact.get("qualifiers") or {},
                ))
            except Exception:
                continue

        logger.info(
            "Extraction parsed %d facts: %s",
            len(extracted),
            [f.content for f in extracted],
        )

        return extracted

extraction_service = ExtractionService()
