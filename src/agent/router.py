import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from langchain_core.messages import BaseMessage
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from src.agent.agent import chat
from src.agent.session import ConversationSession, session_store
from src.agent.tools import build_tools
from src.auth.models import User
from src.auth.service import get_current_user
from src.database import get_session
from src.wallet.repository import WalletRepository

router = APIRouter()


class ChatRequest(BaseModel):
    message: str
    session_id: uuid.UUID | None = None


class ChatResponse(BaseModel):
    session_id: uuid.UUID
    answer: str


class SessionResponse(BaseModel):
    session_id: uuid.UUID
    messages: list[dict]


def _serialize_message(msg: BaseMessage) -> dict:
    # LangChain's message .type values ("human", "ai", "tool", "system")
    # aren't the vocabulary an API consumer expects — translate to
    # conventional chat-role names.
    role = {"human": "user", "ai": "assistant", "tool": "tool", "system": "system"}.get(msg.type, msg.type)
    content = msg.content if isinstance(msg.content, str) else str(msg.content)
    return {"role": role, "content": content}


@router.post("/chat", response_model=ChatResponse)
async def agent_chat(
    body: ChatRequest,
    current_user: User = Depends(get_current_user),
    session: AsyncSession = Depends(get_session),
):
    # A-05: get_or_create both starts a fresh session and continues an
    # existing one — but only if that session actually belongs to this
    # user (checked inside session_store.get_or_create). Passing someone
    # else's session_id silently starts a brand-new session instead of
    # erroring, so this endpoint can't be used to probe whether a given
    # session_id exists and who owns it.
    conv_session: ConversationSession = session_store.get_or_create(body.session_id, current_user.id)

    repo = WalletRepository(session)
    tools = build_tools(repo, current_user.id)

    answer = await chat(body.message, conv_session, tools)
    return ChatResponse(session_id=conv_session.id, answer=answer)


@router.get("/sessions/{session_id}", response_model=SessionResponse)
async def get_session_history(
    session_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
):
    conv_session = session_store.get(session_id)
    # Same 404 for "doesn't exist" and "exists but belongs to someone else" —
    # deliberately indistinguishable, so this endpoint can't be used to
    # enumerate valid session ids that belong to other users.
    if conv_session is None or conv_session.user_id != current_user.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Session not found")

    return SessionResponse(
        session_id=conv_session.id,
        messages=[_serialize_message(m) for m in conv_session.messages],
    )