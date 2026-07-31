"""Deterministic, schema-aware multi-route context selection."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from app.routing_trace import log_routing_event


class RetrievalSource(str, Enum):
    MEMORY = "memory"
    KNOWLEDGE = "knowledge"
    CONVERSATION = "conversation"


@dataclass(frozen=True)
class RoutingDecision:
    normalized_query: str
    subject: str
    matched_memory_patterns: list[str]
    matched_enterprise_terms: list[str]
    enterprise_fields: list[Any]
    personal_memory_fallback: bool
    candidate_confidence: dict[RetrievalSource, float]
    selected_routes: list[RetrievalSource]
    reasons: dict[RetrievalSource, list[str]] = field(default_factory=dict)


class QueryRouter:
    """Select independent context sources; no selected source suppresses another."""

    _enterprise_terms = {
        "leave", "salary", "laptop", "vpn", "budget", "travel", "department",
        "finance", "inventory", "production", "machine", "hr",
    }
    _memory_patterns = (
        r"\bwhat do you remember\b", r"\bdo you remember\b",
        r"\bmy (?:name|preference|preferences|favorite|favourite)\b",
        r"\bwhat (?:is|are) my (?:name|preference|preferences|favorite|favourite)\b",
        r"\b(?:recall|remember|memory|memories)\b",
        r"\b(?:list|show|tell me)\s+(?:everything|all)\s+(?:you\s+)?know about me\b",
        r"\bwho(?:m)? do i report to\b",
        r"\bwho(?: is|'s) my (?:manager|supervisor)\b",
        r"\btell me about (?:myself|me)\b",
        r"\bwhat do you know about me\b",
        r"\b(?:which|what) company do i work (?:in|at|for)\b",
        r"\bwhere do i work\b", r"\bwhat is my employer\b", r"\bwho do i work for\b",
    )
    _complete_profile_patterns = (
        r"\b(?:retrieve|show)\s+(?:all\s+)?my\s+memories\b",
        r"\b(?:list|show|tell me)\s+(?:everything|all)\s+(?:you\s+)?know about me\b",
        r"\btell me about (?:myself|me)\b",
        r"\bwhat do you (?:know|remember) about me\b",
        r"\bwhere do i work\b", r"\bwhat is my employer\b", r"\bwho do i work for\b",
    )
    _personal_question_pattern = re.compile(r"\b(?:what|which|where|who|whom|how)\b.*\b(?:my|myself|mine)\b")

    def __init__(self, enterprise_field_resolver: Callable[[str], list[Any]] | None = None):
        self.enterprise_field_resolver = enterprise_field_resolver or self._resolve_enterprise_fields

    @staticmethod
    def _resolve_enterprise_fields(query: str) -> list[Any]:
        # Lazy import avoids a router <-> knowledge module initialization cycle.
        from app.services.knowledge import knowledge_service
        return knowledge_service.match_fields(query)

    def analyze(self, query: str) -> RoutingDecision:
        normalized = query.lower().strip()
        tokens = set(re.findall(r"[a-z0-9_]+", normalized))
        matched_memory = [pattern for pattern in self._memory_patterns if re.search(pattern, normalized)]
        complete_profile_request = any(re.search(pattern, normalized) for pattern in self._complete_profile_patterns)
        complete_profile_request = complete_profile_request or (
            sum(marker in normalized for marker in ("where", "report", "colleagues", "team")) >= 2
        )
        enterprise_fields = self.enterprise_field_resolver(normalized)
        matched_terms = sorted(tokens & self._enterprise_terms)
        personal_fallback = bool(self._personal_question_pattern.search(normalized))
        enterprise_match = bool(enterprise_fields) or self._legacy_enterprise_match(normalized, tokens)

        memory_confidence = 1.0 if matched_memory else (0.55 if personal_fallback and not enterprise_match else 0.0)
        knowledge_confidence = 1.0 if enterprise_fields else (0.80 if enterprise_match else 0.0)
        candidate_confidence = {
            RetrievalSource.MEMORY: memory_confidence,
            RetrievalSource.KNOWLEDGE: knowledge_confidence,
            RetrievalSource.CONVERSATION: 1.0,
        }
        routes = []
        reasons: dict[RetrievalSource, list[str]] = {}
        if memory_confidence:
            routes.append(RetrievalSource.MEMORY)
            reasons[RetrievalSource.MEMORY] = (["explicit_memory_intent"] if matched_memory else ["personal_question_without_enterprise_match"])
            if complete_profile_request:
                reasons[RetrievalSource.MEMORY].append("complete_profile_request")
        if knowledge_confidence:
            routes.append(RetrievalSource.KNOWLEDGE)
            field_reasons = [f"schema_field:{field.sheet_name}.{field.column_name}" for field in enterprise_fields]
            reasons[RetrievalSource.KNOWLEDGE] = field_reasons or [f"legacy_enterprise_term:{term}" for term in matched_terms] or ["employee_record_request"]
        routes.append(RetrievalSource.CONVERSATION)
        reasons[RetrievalSource.CONVERSATION] = ["always_available_follow_up_context"]

        decision = RoutingDecision(
            normalized_query=normalized,
            # The authenticated principal is the subject by default, including
            # unqualified employee-field requests such as "What is project code?".
            subject="authenticated_user",
            matched_memory_patterns=matched_memory,
            matched_enterprise_terms=matched_terms,
            enterprise_fields=enterprise_fields,
            personal_memory_fallback=personal_fallback,
            candidate_confidence=candidate_confidence,
            selected_routes=routes,
            reasons=reasons,
        )
        log_routing_event(
            "router_decision",
            normalized_query=decision.normalized_query,
            subject=decision.subject,
            matched_memory_patterns=decision.matched_memory_patterns,
            matched_enterprise_terms=decision.matched_enterprise_terms,
            enterprise_fields=[{"sheet": field.sheet_name, "field": field.column_name} for field in enterprise_fields],
            personal_memory_fallback=decision.personal_memory_fallback,
            candidate_confidence={source.value: score for source, score in decision.candidate_confidence.items()},
            selected_routes=[source.value for source in decision.selected_routes],
            reasons={source.value: value for source, value in decision.reasons.items()},
        )
        return decision

    def route(self, query: str) -> list[RetrievalSource]:
        """Compatibility wrapper for callers that only need route names."""
        return self.analyze(query).selected_routes

    @staticmethod
    def _legacy_enterprise_match(query: str, tokens: set[str]) -> bool:
        return (
            bool(tokens & QueryRouter._enterprise_terms)
            or bool(re.search(r"\b(?:my|all)\s+(?:records?|profile)\b", query))
            or bool(re.search(r"\be\d{4}\b", query))
            or ("employee" in tokens and "which" in tokens)
        )


query_router = QueryRouter()
