"""
Enterprise Agent Runner

Starts an interactive chat session
with the Enterprise Agent.
"""
from agent.agent import enterprise_agent
from agent.config import DEMO_USER_ID

def main():
    """
    Start the Enterprise Agent.
    """
    print("=" * 60)
    print("Enterprise AI Assistant")
    print("=" * 60)
    print("Type 'exit' to quit.\n")

    user_id = DEMO_USER_ID
    print(f"Demo identity: {user_id}")

    print()

    while True:
        query = input("You: ").strip()

        if query.lower() in ["exit", "quit"]:
            print("\nGoodbye!")
            break

        try:
            response = enterprise_agent.chat(
                user_id=user_id,
                query=query
            )
            print("\nAssistant:")
            print(response)
            print()

        except Exception as error:
            print("\nError:")
            print(error)
            print()

if __name__ == "__main__":
    main()
