"""
Integration tests for the agent chat endpoints.

These tests hit the FastAPI app end to end. The LLM must be stubbed — do NOT
make real calls to Anthropic/OpenAI. Inject a fake chat model (or patch
`src.agent.agent.get_llm`) that returns deterministic tool calls and answers,
so the tool-call → result → answer contract can be asserted without a network.

Covers BUSINESS_SPEC A-01..A-06: scoped tool calls, multi-turn history, and
graceful handling of empty/ambiguous queries.
"""
import pytest
from httpx import AsyncClient


class TestAgentChat:
    async def test_chat_requires_authentication(self, client: AsyncClient):
        # POST /agent/chat without a JWT must be rejected
        pytest.skip("not implemented")

    async def test_chat_answers_grounded_in_tool_results(self, client: AsyncClient):
        # 1. Register a user, obtain a JWT, seed some transactions
        # 2. POST /agent/chat with a question that triggers a tool call
        # 3. Assert the answer reflects the (stubbed) tool result, not a hallucination
        pytest.skip("not implemented")

    async def test_multi_turn_history_is_preserved(self, client: AsyncClient):
        # First turn returns a session_id; second turn reuses it and the agent
        # can resolve a follow-up that depends on the prior turn's context
        pytest.skip("not implemented")

    async def test_tool_calls_are_scoped_to_the_caller(self, client: AsyncClient):
        # User A cannot retrieve User B's data through the agent
        pytest.skip("not implemented")

    async def test_handles_empty_result_gracefully(self, client: AsyncClient):
        # A question whose tools return nothing yields a clear "no data" answer
        pytest.skip("not implemented")


class TestSessionHistory:
    async def test_get_session_returns_conversation_history(self, client: AsyncClient):
        # GET /agent/sessions/{id} returns the messages of an owned session
        pytest.skip("not implemented")

    async def test_cannot_read_another_users_session(self, client: AsyncClient):
        pytest.skip("not implemented")
