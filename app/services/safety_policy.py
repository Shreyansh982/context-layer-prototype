"""Deterministic policy checks for actions that must not be performed in chat."""

import re


class SafetyPolicy:
    """Keeps destructive data operations outside the conversational surface."""

    _destructive_verb = re.compile(
        r"\b(?:delete|clear|erase|remove|purge|forget)\b", re.IGNORECASE
    )
    _memory_target = re.compile(
        r"\b(?:memory|memories|conversation|history|context|"
        r"saved (?:data|information|facts)|personal data|records?)\b",
        re.IGNORECASE,
    )

    refusal = (
        "I am not allowed to delete, clear, or alter memories, conversation "
        "history, or stored records through chat."
    )

    def refusal_for(self, query: str) -> str | None:
        if self._destructive_verb.search(query) and self._memory_target.search(query):
            return self.refusal
        return None


safety_policy = SafetyPolicy()
