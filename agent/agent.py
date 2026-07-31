"""
Enterprise Agent

Thin client over the Context Layer.
"""
import re

from agent.calculator import calculator
from agent.context_tool import context_tool
from agent.datetime_tool import datetime_tool

class EnterpriseAgent:
    def chat(
        self,
        user_id: str,
        query: str
    ) -> str:
        query_lower = query.lower()

        # Date / Time
        
        if (
            "date" in query_lower
            or "time" in query_lower
        ):
            return datetime_tool.now()

        # Calculator
        
        if re.fullmatch(
            r"[0-9\+\-\*\/\(\)\.\s]+",
            query
        ):
            return calculator.calculate(query)

        if query_lower.startswith("calculate"):
            expression = query[9:].strip()

            return calculator.calculate(expression)

        # Everything else goes to Context Layer

        response = context_tool.ask(
            user_id=user_id,
            query=query
        )

        return response["answer"]

enterprise_agent = EnterpriseAgent()