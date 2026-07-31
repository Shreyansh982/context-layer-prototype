"""
Context Layer Tool

Communicates with the Context Layer
through its REST API.
"""
import requests
from agent.config import AGENT_ID, CONTEXT_LAYER_URL, DEMO_API_KEY

class ContextTool:
    """
    Client for the Context Layer.
    """
    def __init__(self):
        self.base_url = CONTEXT_LAYER_URL

    def ask(
        self,
        user_id: str,
        query: str
    ) -> dict:
        """
        Send a query to the Context Layer.
        """
        response = requests.post(
            f"{self.base_url}/query",
            json={
                "user_id": user_id,
                "agent_id": AGENT_ID,
                "query": query
            },
            headers={"X-Demo-API-Key": DEMO_API_KEY or ""},
            timeout=60
        )
        response.raise_for_status()

        return response.json()

    def health(self) -> dict:
        """
        Check whether the Context Layer
        is running.
        """
        response = requests.get(
            f"{self.base_url}/ready",
            timeout=10
        )
        response.raise_for_status()

        return response.json()

context_tool = ContextTool()
