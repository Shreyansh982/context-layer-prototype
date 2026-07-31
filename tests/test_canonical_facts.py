from datetime import datetime

from app.models import ExtractedFact, MemoryScope
from app.services.facts import CanonicalFactService, fact_normalizer
from app.services.enterprise_sync import enterprise_sync_service


def test_compound_work_statement_becomes_two_canonical_facts():
    facts = fact_normalizer.normalize(
        ExtractedFact(content="User works in the AI team at Jindal Steel Ltd"), "E0001"
    )

    assert [(fact.predicate, fact.object_key) for fact in facts] == [
        ("MEMBER_OF", "team:ai-team"),
        ("WORKS_AT", "organization:jindal-steel"),
    ]


def test_manager_wording_resolves_to_one_fact_identity():
    left = fact_normalizer.normalize(ExtractedFact(content="User manager is Gyan Gupta"), "E0001")[0]
    right = fact_normalizer.normalize(ExtractedFact(content="User reports to Gyan Gupta"), "E0001")[0]

    assert left.predicate == right.predicate == "REPORTS_TO"
    assert CanonicalFactService.fact_key(left) == CanonicalFactService.fact_key(right)


def test_first_person_aliases_always_resolve_to_authenticated_profile():
    for alias in ("I", "me", "my", "mine", "myself", "USER"):
        key, entity_type, display = fact_normalizer._entity(alias, "PERSON", "E0001")
        assert (key, entity_type, display) == ("person:E0001", "PERSON", "E0001")

    fact = fact_normalizer.normalize(
        ExtractedFact(content="I work at Jindal Steel", subject="I", predicate="WORKS_AT", object="Jindal Steel", object_type="ORGANIZATION"),
        "E0001",
    )[0]
    assert fact.subject_key == "person:E0001"


def test_structured_extraction_preserves_non_user_subjects():
    manager_relationship = fact_normalizer.normalize(
        ExtractedFact(
            content="My manager is Gyan Gupta.", subject="USER", predicate="REPORTS_TO",
            object="Gyan Gupta", object_type="PERSON",
        ),
        "E0001",
    )[0]
    manager_title = fact_normalizer.normalize(
        ExtractedFact(
            content="He is VP of IT.", subject="Gyan Gupta", predicate="HAS_FIELD",
            object="VP of IT", object_type="VALUE", qualifiers={"field": "TITLE"},
        ),
        "E0001",
    )[0]

    assert manager_relationship.subject_key == "person:E0001"
    assert manager_title.subject_key == "person:E0001:gyan-gupta"
    assert manager_title.owner_id == "E0001"
    assert manager_title.display_text.startswith("Gyan Gupta")


def test_colleague_subject_is_not_rewritten_to_authenticated_user():
    fact = fact_normalizer.normalize(
        ExtractedFact(
            content="Abhishek is my colleague.", subject="Abhishek", predicate="COLLEAGUE",
            object="USER", object_type="PERSON",
        ),
        "E0001",
    )[0]

    assert fact.subject_key == "person:E0001:abhishek"
    assert fact.object_key == "person:E0001"
    assert fact.owner_id == "E0001"


def test_first_person_possessive_subject_remains_authenticated_user():
    fact = fact_normalizer.normalize(
        ExtractedFact(
            content="My name is Shreyansh.", subject="my", predicate="HAS_NAME",
            object="Shreyansh", object_type="VALUE",
        ),
        "E0001",
    )[0]

    assert fact.subject_key == "person:E0001"
    assert fact.display_text == "User's name is Shreyansh"


def test_authenticated_employee_id_is_not_an_external_person_or_self_colleague():
    key, entity_type, display = fact_normalizer._entity("E0001", "PERSON", "E0001")
    assert (key, entity_type, display) == ("person:E0001", "PERSON", "E0001")
    candidates = fact_normalizer.normalize(
        ExtractedFact(content="My colleague is E0001", predicate="COLLEAGUE", object="E0001", object_type="PERSON"),
        "E0001",
    )
    assert candidates == []


def test_combined_colleagues_become_individual_facts():
    facts = fact_normalizer.normalize(
        ExtractedFact(content="Abhishek and Aayush are colleague interns"), "E0001"
    )

    assert {(fact.predicate, fact.object_name) for fact in facts} == {
        ("COLLEAGUE", "Abhishek"), ("COLLEAGUE", "Aayush")
    }


def test_organization_aliases_have_the_same_fact_identity():
    short = fact_normalizer.normalize(ExtractedFact(content="User works at Jindal Steel"), "E0001")[0]
    long = fact_normalizer.normalize(ExtractedFact(content="User works at Jindal Steel Ltd"), "E0001")[0]

    assert CanonicalFactService.fact_key(short) == CanonicalFactService.fact_key(long)


def test_enterprise_authority_wins_for_single_valued_predicates():
    service = CanonicalFactService()
    now = datetime.now().isoformat()
    records = [
        {"fact_key": "conversation", "display_text": "User reports to A", "scope": MemoryScope.USER.value,
         "owner_id": "E0001", "first_seen": now, "last_seen": now, "times_observed": 1,
         "source_record_key": None, "predicate": "REPORTS_TO", "authority_rank": 20},
        {"fact_key": "enterprise", "display_text": "User reports to M008", "scope": MemoryScope.USER.value,
         "owner_id": "E0001", "first_seen": now, "last_seen": now, "times_observed": 1,
         "source_record_key": "excel:HR:E0001", "predicate": "REPORTS_TO", "authority_rank": 100},
    ]

    assert [fact.id for fact in service._select_authoritative(records)] == ["enterprise"]


def test_enterprise_row_fields_use_atomic_predicates_not_a_full_row_sentence():
    leave = enterprise_sync_service._candidate("E0001", "LEAVE_BAL", 22)
    manager = enterprise_sync_service._candidate("E0001", "MANAGER_ID", "M008")
    generic = enterprise_sync_service._candidate("E0001", "CUSTOM_FIELD", "value")

    assert (leave.predicate, leave.value) == ("HAS_LEAVE_BALANCE", "22")
    assert (manager.predicate, manager.object_type, manager.object_key) == ("REPORTS_TO", "PERSON", "person:E0001:m008")
    assert generic.predicate == "HAS_FIELD"
    assert generic.qualifiers == {"field": "CUSTOM_FIELD"}
