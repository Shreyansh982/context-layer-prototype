"""Controlled canonical-predicate vocabulary.

The application deliberately keeps predicate governance in code for the demo,
but all consumers depend on :class:`PredicateRegistry` rather than this
implementation.  A YAML or database registry can replace it later without
changing normalization or persistence callers.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from types import MappingProxyType


@dataclass(frozen=True)
class PredicateDefinition:
    name: str
    storage: str = "scalar"  # scalar | entity | note
    object_type: str = "VALUE"
    aliases: tuple[str, ...] = ()
    profile_dimension: str | None = None
    values: tuple[str, ...] = ()


class PredicateRegistry(ABC):
    @abstractmethod
    def resolve(self, value: str | None) -> PredicateDefinition | None: ...

    @abstractmethod
    def definition(self, name: str) -> PredicateDefinition | None: ...

    @property
    @abstractmethod
    def names(self) -> frozenset[str]: ...


class PythonPredicateRegistry(PredicateRegistry):
    """Immutable in-code registry for the Version 2 vocabulary."""

    def __init__(self, definitions: tuple[PredicateDefinition, ...]):
        by_name = {definition.name: definition for definition in definitions}
        aliases = {
            alias: definition.name
            for definition in definitions
            for alias in definition.aliases
        }
        self._by_name = MappingProxyType(by_name)
        self._aliases = MappingProxyType(aliases)

    def resolve(self, value: str | None) -> PredicateDefinition | None:
        if not value:
            return None
        normalized = "_".join("".join(char if char.isalnum() else " " for char in value.upper()).split())
        return self._by_name.get(self._aliases.get(normalized, normalized))

    def definition(self, name: str) -> PredicateDefinition | None:
        return self._by_name.get(name)

    @property
    def names(self) -> frozenset[str]:
        return frozenset(self._by_name)


_DEFINITIONS = (
    PredicateDefinition("WORKS_AT", "entity", "ORGANIZATION", ("WORKS_FOR", "EMPLOYED_BY")),
    PredicateDefinition("MEMBER_OF", "entity", "TEAM", ("WORKS_IN", "PART_OF")),
    PredicateDefinition("REPORTS_TO", "entity", "PERSON", ("MANAGED_BY", "MANAGER_IS")),
    PredicateDefinition("COLLEAGUE", "entity", "PERSON", ("COLLEAGUE_OF",)),
    PredicateDefinition("HAS_NAME"),
    PredicateDefinition("PREFERS", aliases=("LIKES", "PREFERENCES")),
    PredicateDefinition("PROFILE_NOTE", "note"),
    PredicateDefinition("PREF_RESPONSE_FORMAT", profile_dimension="response_format", values=("BULLETS", "PARAGRAPHS")),
    PredicateDefinition("PREF_RESPONSE_LENGTH", profile_dimension="response_length", values=("CONCISE", "DETAILED")),
    PredicateDefinition("PREF_TECHNICAL_DEPTH", profile_dimension="technical_depth", values=("BEGINNER", "ADVANCED")),
    PredicateDefinition("PREF_TONE", profile_dimension="tone", values=("PROFESSIONAL", "CASUAL")),
    PredicateDefinition("HAS_ENTERPRISE_ATTRIBUTE"),
    PredicateDefinition("HAS_FIELD"),
    PredicateDefinition("HAS_GRADE"), PredicateDefinition("HAS_SHIFT"),
    PredicateDefinition("HAS_LEAVE_BALANCE"), PredicateDefinition("HAS_SICK_LEAVE_BALANCE"),
    PredicateDefinition("HAS_LAPTOP_MODEL"), PredicateDefinition("HAS_VPN_ACCESS"),
    PredicateDefinition("HAS_STORAGE_GB"), PredicateDefinition("HAS_PROJECT_CODE"),
    PredicateDefinition("HAS_STATUS"), PredicateDefinition("HAS_COST_CENTER"),
    PredicateDefinition("HAS_TRAVEL_LIMIT"), PredicateDefinition("HAS_MONTHLY_BUDGET"),
)


predicate_registry: PredicateRegistry = PythonPredicateRegistry(_DEFINITIONS)
