"""
Agent Configuration

Stores configuration values used by the
Enterprise Agent.
"""
import os
from dotenv import load_dotenv

load_dotenv()

# Groq

GROQ_API_KEY = os.getenv("GROQ_API_KEY")
MODEL_NAME = "llama-3.3-70b-versatile"

# Context Layer

CONTEXT_LAYER_URL = os.getenv("CONTEXT_LAYER_URL", "http://127.0.0.1:8000")
DEMO_API_KEY = os.getenv("DEMO_API_KEY")
DEMO_USER_ID = os.getenv("DEMO_USER_ID", "E0001")

# Agent

AGENT_ID = os.getenv("AGENT_ID", "enterprise_agent_cli")
MAX_TOOL_ITERATIONS = 5
