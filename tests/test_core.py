import unittest

from agent.calculator import CalculatorTool
from app.models import QueryRequest
from app.router import RetrievalSource, QueryRouter
from app.services.safety_policy import SafetyPolicy


class CalculatorToolTests(unittest.TestCase):
    def test_evaluates_supported_math(self):
        self.assertEqual(CalculatorTool().calculate("2 * (3 + 4)"), "14")

    def test_rejects_python_attribute_access(self):
        result = CalculatorTool().calculate("().__class__")
        self.assertTrue(result.startswith("Calculation Error:"))


class QueryRouterTests(unittest.TestCase):
    def test_general_question_includes_conversation_only(self):
        routes = QueryRouter().route("What can you tell me?")
        self.assertEqual(routes, [RetrievalSource.CONVERSATION])

    def test_domain_question_routes_only_to_knowledge(self):
        routes = QueryRouter().route("What is the leave policy?")
        self.assertIn(RetrievalSource.KNOWLEDGE, routes)

    def test_memory_question_does_not_route_to_knowledge(self):
        routes = QueryRouter().route("What is my name?")
        self.assertEqual(routes, [RetrievalSource.MEMORY, RetrievalSource.CONVERSATION])

    def test_reporting_and_self_summary_questions_route_to_memory(self):
        for query in (
            "Whom do I report to?",
            "Who is my manager?",
            "Who's my manager?",
            "Tell me about myself",
            "Tell me about me",
            "What do you know about me?",
        ):
            with self.subTest(query=query):
                routes = QueryRouter().route(query)
                self.assertIn(RetrievalSource.MEMORY, routes)
                if "manager" not in query.lower():
                    self.assertNotIn(RetrievalSource.KNOWLEDGE, routes)

    def test_company_and_employer_questions_route_to_memory(self):
        for query in (
            "Which company do I work in?",
            "Which company do I work at?",
            "What company do I work for?",
            "Where do I work?",
            "What is my employer?",
        ):
            with self.subTest(query=query):
                routes = QueryRouter().route(query)
                self.assertIn(RetrievalSource.MEMORY, routes)
                self.assertNotIn(RetrievalSource.KNOWLEDGE, routes)

    def test_general_knowledge_questions_do_not_route_to_memory_or_knowledge(self):
        for query in ("What is NLTK?", "Do you believe in ghosts?"):
            with self.subTest(query=query):
                routes = QueryRouter().route(query)
                self.assertNotIn(RetrievalSource.MEMORY, routes)
                self.assertNotIn(RetrievalSource.KNOWLEDGE, routes)
                self.assertEqual(routes, [RetrievalSource.CONVERSATION])

    def test_personal_question_fallback_handles_unlisted_memory_phrasing(self):
        routes = QueryRouter().route("Who are my project contacts?")
        self.assertIn(RetrievalSource.MEMORY, routes)
        self.assertNotIn(RetrievalSource.KNOWLEDGE, routes)

    def test_personal_question_fallback_does_not_run_for_fast_path_or_enterprise_match(self):
        router = QueryRouter()
        calls = []
        router._is_personal_memory_fallback = lambda query: calls.append(query) or False

        router.route("Where do I work?")
        router.route("What is my leave balance?")

        self.assertEqual(calls, [])

    def test_personal_enterprise_field_question_uses_authoritative_knowledge(self):
        routes = QueryRouter().route("What is my laptop model?")
        self.assertIn(RetrievalSource.KNOWLEDGE, routes)
        self.assertNotIn(RetrievalSource.MEMORY, routes)

    def test_personal_record_request_includes_knowledge(self):
        routes = QueryRouter().route("Retrieve all my records")
        self.assertIn(RetrievalSource.KNOWLEDGE, routes)

    def test_connected_employee_question_includes_knowledge(self):
        routes = QueryRouter().route("Which employee am I?")
        self.assertIn(RetrievalSource.KNOWLEDGE, routes)


class UserIdValidationTests(unittest.TestCase):
    def test_employee_id_is_normalized(self):
        self.assertEqual(QueryRequest(user_id=" e0002 ", agent_id="agent-a", query="hello").user_id, "E0002")

    def test_legacy_id_is_rejected(self):
        with self.assertRaises(ValueError):
            QueryRequest(user_id="U007", agent_id="agent-a", query="hello")

    def test_rejects_unstructured_user_id(self):
        with self.assertRaises(ValueError):
            QueryRequest(user_id="alice", agent_id="agent-a", query="hello")


class SafetyPolicyTests(unittest.TestCase):
    def test_refuses_memory_deletion_request(self):
        response = SafetyPolicy().refusal_for("Please delete my memory")
        self.assertEqual(response, SafetyPolicy.refusal)

    def test_allows_a_non_destructive_memory_question(self):
        self.assertIsNone(SafetyPolicy().refusal_for("What do you remember about me?"))

if __name__ == "__main__":
    unittest.main()
