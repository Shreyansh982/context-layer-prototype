from datetime import datetime

from app.models import ContextPackage, MemoryFact, MemoryScope
from app.prompt_builder import PromptBuilder


def test_legacy_knowledge_memory_is_rendered_as_readable_language():
    memory = MemoryFact(
        id="1", owner_id="E0004", agent_id="agent-a", scope=MemoryScope.USER, confidence=1,
        timestamp=datetime.now(), source="knowledge_lookup",
        content='{"sheet":"IT","row":{"EMP_ID":"E0004","LAPTOP_MODEL":"LTP006"}}',
    )

    prompt = PromptBuilder().build("What is my laptop?", ContextPackage(memories=[memory]))

    assert "Employee E0004 record: laptop model: LTP006." in prompt
    assert '"sheet"' not in prompt
