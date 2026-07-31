from app.router import QueryRouter, RetrievalSource
from app.services.knowledge import KnowledgeService


def test_router_handles_the_six_rca_queries_with_schema_driven_knowledge():
    router = QueryRouter()
    expected = {
        "Tell me about myself": {RetrievalSource.MEMORY, RetrievalSource.CONVERSATION},
        "Retrieve all memories related to me": {RetrievalSource.MEMORY, RetrievalSource.CONVERSATION},
        "Tell me my leave balance": {RetrievalSource.KNOWLEDGE, RetrievalSource.CONVERSATION},
        "What is my project code?": {RetrievalSource.KNOWLEDGE, RetrievalSource.CONVERSATION},
        "What is project code?": {RetrievalSource.KNOWLEDGE, RetrievalSource.CONVERSATION},
        "What is my status?": {RetrievalSource.KNOWLEDGE, RetrievalSource.CONVERSATION},
    }
    for query, routes in expected.items():
        assert set(router.route(query)) == routes


def test_schema_discovery_matches_workbook_fields_without_router_keywords():
    service = KnowledgeService()
    fields = {(field.sheet_name, field.column_name) for field in service.match_fields("What is my project code and leave balance?")}
    assert ("Finance", "PROJECT_CODE") in fields
    assert ("HR", "LEAVE_BAL") in fields


def test_authenticated_field_lookup_uses_private_employee_row_not_public_status_rows():
    service = KnowledgeService()
    records = service.search("What is my status?", requesting_user_id="E0004")
    assert [(record.sheet_name, record.row_data["EMP_ID"]) for record in records] == [("IT", "E0004")]
