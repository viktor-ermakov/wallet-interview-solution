# Technical Specification — Digital Wallet

## 1. Technology Stack

| Layer               | Choice                                      | Rationale                                             |
|----------------------|----------------------------------------------|--------------------------------------------------------|
| Language            | Python 3.11+                                | Required                                              |
| Web Framework       | FastAPI                                     | Async, typed, automatic docs, industry standard        |
| ORM                 | SQLAlchemy 2.x (async)                      | Async sessions, fine-grained lock control              |
| Database            | PostgreSQL 15                               | ACID, row-level locking for balance                    |
| Migrations          | Alembic                                     | Schema versioning alongside SQLAlchemy                 |
| Authentication      | python-jose + passlib                       | JWT HS256, bcrypt hashing                              |
| HTTP Client         | httpx (async)                               | Calls to the gateway API                                |
| AI Agent            | LangChain (`langchain-core` + adapter)      | Provider abstraction — swap the LLM via config          |
| Validation          | Pydantic v2                                 | Request/response schemas, settings                     |
| Testing             | pytest + pytest-asyncio + httpx             | Async test client                                       |
| LLM (default)       | `langchain-anthropic` (Claude)               | Default adapter; swap for another without changing code |
| Container           | Docker + Docker Compose                     | Required runtime environment                            |

---

## 2. Project Structure

Legend: **(skeleton)** already included in the repository · **(you implement)** you must create/complete.

```
wallet-interview/
├── docs/
│   ├── BUSINESS_SPEC.md
│   ├── TECHNICAL_SPEC.md
│   └── EVALUATION_CRITERIA.md
│
├── src/
│   ├── wallet/                    — core domain
│   │   ├── models.py              — ORM models (SQLAlchemy)                (skeleton)
│   │   ├── schemas.py             — Pydantic request/response schemas      (skeleton)
│   │   ├── service.py             — business logic (no HTTP/DB)            (skeleton — to complete)
│   │   ├── repository.py          — DB queries (SQLAlchemy sessions)       (skeleton — to complete)
│   │   └── router.py              — FastAPI routes                         (skeleton — to complete)
│   │
│   ├── gateway/                   — payment gateway abstraction
│   │   ├── base.py                — PaymentGateway interface (Protocol)    (skeleton)
│   │   ├── webhook_handler.py     — webhook verification + dispatch        (skeleton — to complete)
│   │   └── <provider>_gateway.py  — concrete implementation (Stripe/MP)    (you implement)
│   │
│   ├── agent/                     — query agent
│   │   ├── tools.py               — tool definitions                       (skeleton — to complete)
│   │   ├── session.py             — conversation session management       (skeleton)
│   │   └── agent.py               — LangChain loop with tool use           (skeleton — to complete)
│   │
│   ├── auth/                      — authentication
│   │   ├── models.py                                                       (skeleton)
│   │   ├── schemas.py                                                      (skeleton)
│   │   ├── service.py                                                      (skeleton — to complete)
│   │   └── router.py                                                       (skeleton — to complete)
│   │
│   ├── database.py                — async engine + session factory         (skeleton)
│   ├── config.py                  — Pydantic Settings (env vars)           (skeleton)
│   └── main.py                    — FastAPI app factory + routers           (skeleton)
│
├── tests/
│   ├── unit/
│   │   ├── test_wallet_service.py                                          (skeleton — cases to implement)
│   │   └── test_agent_tools.py                                            (skeleton — cases to implement)
│   └── integration/
│       ├── test_deposit_flow.py                                           (skeleton — cases to implement)
│       └── test_agent_chat.py                                             (skeleton — cases to implement)
│
│   # Schema creation is your decision (see §4 / §5). If you choose Alembic,
│   # initialize it (`alembic init alembic`) — it is not included in the skeleton.
│
├── docker-compose.yml                                                      (skeleton)
├── Dockerfile                                                              (skeleton)
├── pyproject.toml                                                          (skeleton)
├── .env.example                                                            (skeleton)
└── README.md                                                              (to complete — design decisions section)
```

---

## 3. Environment Variables

```bash
# Application
DATABASE_URL=postgresql+asyncpg://wallet:wallet@db:5432/wallet
SECRET_KEY=<random-256-bit-hex>
ALGORITHM=HS256
ACCESS_TOKEN_EXPIRE_MINUTES=60

# Gateway — choose one
GATEWAY_PROVIDER=stripe            # or mercadopago
STRIPE_SECRET_KEY=sk_test_...
STRIPE_WEBHOOK_SECRET=whsec_...
# MERCADO_PAGO_ACCESS_TOKEN=...

# Anthropic
ANTHROPIC_API_KEY=sk-ant-...
ANTHROPIC_MODEL=claude-sonnet-4-6
```

---

## 4. Design Decisions

### 4.1 Balance Atomicity

Use `SELECT ... FOR UPDATE` (pessimistic locking) on the wallet row before any balance
mutation. This ensures that concurrent requests are naturally serialized at the database
level.

```python
# repository.py — example pattern
async def get_wallet_for_update(session: AsyncSession, wallet_id: UUID) -> Wallet:
    result = await session.execute(
        select(Wallet)
        .where(Wallet.id == wallet_id)
        .with_for_update()
    )
    return result.scalar_one()
```

> **Note:** operations on a single wallet (deposit, withdrawal) are straightforward. Transfers
> between users lock **two** wallet rows within the same transaction. Consider the
> implications of lock acquisition order in this scenario.

### 4.2 Gateway Abstraction

All interactions with the gateway go through the `PaymentGateway` protocol. This allows
unit tests to inject a `FakeGateway` without making real HTTP calls.

```python
# gateway/base.py
from typing import Protocol
from decimal import Decimal
from dataclasses import dataclass

@dataclass
class PaymentIntent:
    gateway_reference: str
    client_secret: str | None
    status: str

class PaymentGateway(Protocol):
    async def create_deposit_intent(self, amount: Decimal, currency: str, metadata: dict) -> PaymentIntent: ...
    async def create_payout(self, amount: Decimal, destination: dict, metadata: dict) -> PaymentIntent: ...
    def verify_webhook(self, payload: bytes, signature: str) -> dict: ...
```

### 4.3 Agent with LangChain (Provider-Agnostic)

The agent uses `langchain-core` to abstract the LLM provider. Switching providers is done
solely via environment variable — no code changes required:

```python
# agent/agent.py — conceptual flow
def get_llm() -> BaseChatModel:
    if settings.llm_provider == "anthropic":
        from langchain_anthropic import ChatAnthropic
        return ChatAnthropic(model=settings.llm_model, api_key=settings.llm_api_key)
    if settings.llm_provider == "openai":
        from langchain_openai import ChatOpenAI
        return ChatOpenAI(model=settings.llm_model, api_key=settings.llm_api_key)

async def chat(message: str, session: ConversationSession, tools: list[BaseTool]) -> str:
    llm = get_llm().bind_tools(tools)
    session.messages.append(HumanMessage(content=message))

    while True:
        response = await llm.ainvoke([SystemMessage(content=SYSTEM_PROMPT)] + session.messages)
        session.messages.append(response)

        if not response.tool_calls:
            return response.content

        for tool_call in response.tool_calls:
            result = await tool_map[tool_call["name"]].ainvoke(tool_call["args"])
            session.messages.append(ToolMessage(content=str(result), tool_call_id=tool_call["id"]))
```

Tools are created by `build_tools(repo, user_id)` in `agent/tools.py`, using the `@tool`
decorator from `langchain-core`. Each tool is a closure over `user_id`, ensuring that all
queries stay scoped to the authenticated user.

### 4.4 Webhook Idempotency

The webhook endpoint uses `gateway_reference` as the idempotency key. If a transaction
with the given reference already exists in a terminal state (`COMPLETED`, `FAILED`), the
webhook is acknowledged but no state mutation occurs.

---

## 5. Database Schema (Reference DDL)

```sql
CREATE TABLE users (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email       TEXT NOT NULL UNIQUE,
    name        TEXT NOT NULL,
    hashed_password TEXT NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE wallets (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    user_id     UUID NOT NULL REFERENCES users(id),
    balance     NUMERIC(18, 2) NOT NULL DEFAULT 0 CHECK (balance >= 0),
    currency    CHAR(3) NOT NULL DEFAULT 'BRL',
    status      TEXT NOT NULL DEFAULT 'ACTIVE',
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (user_id)
);

CREATE TABLE transactions (
    id                          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    wallet_id                   UUID NOT NULL REFERENCES wallets(id),
    type                        TEXT NOT NULL,
    amount                      NUMERIC(18, 2) NOT NULL CHECK (amount > 0),
    balance_before              NUMERIC(18, 2) NOT NULL,
    balance_after               NUMERIC(18, 2) NOT NULL,
    status                      TEXT NOT NULL DEFAULT 'PENDING',
    description                 TEXT,
    counterpart_transaction_id  UUID REFERENCES transactions(id),
    gateway_reference           TEXT UNIQUE,
    metadata                    JSONB,
    created_at                  TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX idx_transactions_wallet_created ON transactions (wallet_id, created_at DESC);
CREATE INDEX idx_transactions_type           ON transactions (wallet_id, type);
CREATE INDEX idx_transactions_status         ON transactions (wallet_id, status);
```

---

## 6. Testing Strategy

| Layer               | Approach                                                                |
|----------------------|---------------------------------------------------------------------------|
| Domain / Service    | Unit tests with in-memory fakes — no DB, no gateway, no HTTP             |
| Repository          | Integration tests against a real PostgreSQL instance (Docker)           |
| Gateway             | Unit tests with `httpx.MockTransport` or `respx`                        |
| Agent Tools         | Unit tests verifying queries and per-user data scoping                  |
| API                 | Integration tests using `AsyncClient` against the full FastAPI app      |
| Agent Loop          | Contract tests verifying the tool call → result → response flow         |

---

## 7. Docker Compose

```yaml
# docker-compose.yml (reference)
services:
  api:
    build: .
    ports: ["8000:8000"]
    env_file: .env
    depends_on:
      db:
        condition: service_healthy

  db:
    image: postgres:15
    environment:
      POSTGRES_USER: wallet
      POSTGRES_PASSWORD: wallet
      POSTGRES_DB: wallet
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U wallet"]
      interval: 5s
      retries: 5
```