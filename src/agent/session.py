import uuid
from dataclasses import dataclass, field

from langchain_core.messages import BaseMessage


@dataclass
class ConversationSession:
    id: uuid.UUID
    user_id: uuid.UUID
    messages: list[BaseMessage] = field(default_factory=list)


class InMemorySessionStore:
    def __init__(self) -> None:
        self._sessions: dict[uuid.UUID, ConversationSession] = {}

    def create(self, user_id: uuid.UUID) -> ConversationSession:
        session = ConversationSession(id=uuid.uuid4(), user_id=user_id)
        self._sessions[session.id] = session
        return session

    def get(self, session_id: uuid.UUID) -> ConversationSession | None:
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: uuid.UUID | None, user_id: uuid.UUID) -> ConversationSession:
        if session_id:
            session = self.get(session_id)
            if session and session.user_id == user_id:
                return session
        return self.create(user_id)


session_store = InMemorySessionStore()
