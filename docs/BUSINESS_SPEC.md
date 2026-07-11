# Business Specification — Digital Wallet with Payment Gateway and AI Agent

## 1. Problem

Design and implement a **Digital Wallet** system that allows users to manage their balance,
perform financial transactions through an external payment gateway, and query their transaction
history in natural language through an AI agent.

This project evaluates the candidate's ability to integrate external systems, model clean
domains, build REST APIs, and work with LLM-based agents in a real-world context.

---

## 2. Business Context

A fintech company needs to offer a digital wallet feature in its product. Users must be able to
deposit funds via credit card or PIX (or any gateway method), transfer funds to other users, and
make withdrawals to their registered bank account.

To reduce the load on customer support, the company wants an AI agent capable of answering
questions about transactions in natural language — replacing a manual BI dashboard for end users.

---

## 3. Actors

| Actor          | Description                                                                  |
|----------------|-------------------------------------------------------------------------------|
| `User`         | Authenticated user who owns one or more wallets                              |
| `Admin`        | Internal operator who can view all wallets and freeze accounts               |
| `Gateway`      | External payment provider (Stripe or Mercado Pago) that processes I/O        |
| `AI Agent`     | LLM agent that answers natural language questions about transactions         |

---

## 4. Functional Requirements

### 4.1 Wallet Management

| ID    | Requirement                                                                             |
|-------|------------------------------------------------------------------------------------------|
| W-01  | Each user may have exactly one active wallet                                            |
| W-02  | The wallet stores a current balance (always non-negative)                               |
| W-03  | The wallet can be in one of these states: `ACTIVE`, `FROZEN`, `CLOSED`                  |
| W-04  | Balance updates must be atomic — no race conditions on concurrent requests              |

### 4.2 Deposit (Inflow)

| ID    | Requirement                                                                             |
|-------|------------------------------------------------------------------------------------------|
| D-01  | The user initiates a deposit specifying amount and payment method                        |
| D-02  | The system creates a `PENDING` transaction and calls the payment gateway                 |
| D-03  | After gateway confirmation, the balance is credited and the transaction marked `COMPLETED` |
| D-04  | On gateway failure, the transaction is marked `FAILED` — the balance is never changed     |
| D-05  | The gateway webhook must be verified (signature validation) and idempotent               |

### 4.3 Withdrawal (Outflow)

| ID    | Requirement                                                                             |
|-------|------------------------------------------------------------------------------------------|
| S-01  | The user initiates a withdrawal specifying amount and destination (bank account/PIX key) |
| S-02  | The withdrawal is only allowed if `wallet.balance >= amount`                             |
| S-03  | The balance is debited immediately (reserved) while the gateway processes the payment    |
| S-04  | If the payment fails, the reserved balance is returned to the wallet                     |

### 4.4 Transfer Between Users

| ID    | Requirement                                                                             |
|-------|------------------------------------------------------------------------------------------|
| T-01  | The user can transfer funds to another user identified by email or ID                    |
| T-02  | The source wallet must have sufficient balance                                           |
| T-03  | The debit and credit must occur in a single atomic operation                             |
| T-04  | Transfers are internal — there is no call to the gateway                                 |
| T-05  | A transfer creates two linked transaction records (debit + credit)                       |

### 4.5 Transaction History

| ID    | Requirement                                                                             |
|-------|------------------------------------------------------------------------------------------|
| H-01  | Users can list their own transactions with pagination                                    |
| H-02  | Filters: date range, transaction type, status, amount range                              |
| H-03  | Each record exposes: id, type, amount, currency, status, created_at, description,        |
|       | counterparty (for transfers), gateway_reference (for gateway transactions)               |

### 4.6 Query Agent

| ID    | Requirement                                                                             |
|-------|------------------------------------------------------------------------------------------|
| A-01  | The user sends a natural language question about their own transactions                  |
| A-02  | The agent has access to structured tools to query transaction data                       |
| A-03  | The agent returns a clear and concise natural language answer                            |
| A-04  | The agent must not expose data from other users — all queries are scoped to the caller   |
| A-05  | The agent supports follow-up questions within the same session (multi-turn)              |
| A-06  | The agent can generate simple aggregations: totals, averages, counts, top-N              |

---

## 5. Non-Functional Requirements

| Category         | Requirement                                                                       |
|-------------------|------------------------------------------------------------------------------------|
| Atomicity         | Balance mutations must use database-level locking or optimistic concurrency        |
| Idempotency       | Webhooks and deposit/withdrawal operations must be idempotent                      |
| Security          | All endpoints require JWT authentication; webhooks require HMAC validation         |
| Observability     | Structured logs on every transaction state change                                  |
| Testability       | Domain logic must be testable without infrastructure dependencies                  |
| Portability       | The application runs via Docker; no dependencies on the host                       |

---

## 6. Domain Model

```
User
 ├── id: UUID
 ├── email: str (unique)
 ├── name: str
 └── created_at: datetime

Wallet
 ├── id: UUID
 ├── user_id: UUID (FK → User)
 ├── balance: Decimal (precision=18, scale=2)
 ├── currency: str (ISO 4217, default "BRL")
 ├── status: WalletStatus (ACTIVE | FROZEN | CLOSED)
 └── updated_at: datetime

Transaction
 ├── id: UUID
 ├── wallet_id: UUID (FK → Wallet)
 ├── type: TransactionType (DEPOSIT | WITHDRAWAL | TRANSFER_DEBIT | TRANSFER_CREDIT)
 ├── amount: Decimal (always positive)
 ├── balance_before: Decimal
 ├── balance_after: Decimal
 ├── status: TransactionStatus (PENDING | COMPLETED | FAILED | REVERSED)
 ├── description: str (optional)
 ├── counterpart_transaction_id: UUID (nullable, FK → Transaction)
 ├── gateway_reference: str (nullable — external payment ID)
 ├── metadata: JSONB (gateway payload, PIX key, bank details, etc.)
 └── created_at: datetime
```

---

## 7. Payment Gateway Integration

The candidate may choose **one** of the gateways below. The gateway module must be abstracted
behind a `PaymentGateway` interface so the implementation can be swapped out.

| Option         | Use Case                              | Docs                                          |
|-----------------|----------------------------------------|-----------------------------------------------|
| Stripe          | Card deposits, payouts                 | https://docs.stripe.com                       |
| Mercado Pago    | PIX, boleto, credit card (BR)          | https://www.mercadopago.com.br/developers     |

**Minimum integration requirements:**

- Create a payment intent / preference for deposits
- Receive and verify a webhook to confirm payment
- Initiate a payout/transfer for withdrawals
- Handle gateway errors appropriately (idempotency keys, retries)

---

## 8. Query Agent — Specification

### 8.1 Objective

The agent replaces a manual BI query interface for the end user. It receives a natural language
question and returns an answer grounded in real transaction data.

### 8.2 Architecture

```
User Question
        │
        ▼
┌─────────────┐        tool calls               ┌──────────────────────┐
│  LLM Agent  │ ──────────────────────────────► │  Transaction          │
│ (Claude API)│ ◄────────────────────────────── │  Tools                │
└─────────────┘        tool results             └──────────────────────┘
        │
        ▼
Natural Language Answer
```

### 8.3 Agent Tools

The agent must be equipped with the following tools (implemented as Python functions):

| Tool                      | Description                                                          |
|----------------------------|-----------------------------------------------------------------------|
| `get_wallet_summary`      | Returns current balance, status, and wallet metadata                  |
| `list_transactions`       | Paginated list with filters (date, type, status, amount range)        |
| `aggregate_transactions`  | SUM / AVG / COUNT / MAX / MIN grouped by type, period, or status       |
| `get_top_transactions`    | Returns top N largest / smallest transactions within a period          |
| `get_transaction_detail`  | Returns full details of a transaction by ID                           |

### 8.4 Interaction Examples

```
User: "How much did I spend in total this month?"
Agent: [calls aggregate_transactions(type=WITHDRAWAL, period=current_month)]
Agent: "You spent R$ 1,250.00 this month across 8 transactions."

User: "What was my biggest expense?"
Agent: [calls get_top_transactions(type=WITHDRAWAL, limit=1)]
Agent: "Your largest withdrawal was R$ 500.00 on June 3rd, 2026."

User: "Show me my last 5 deposits."
Agent: [calls list_transactions(type=DEPOSIT, limit=5)]
Agent: "Here are your last 5 deposits: [formatted list]"
```

### 8.5 Constraints

- The agent must be implemented with LangChain, allowing the LLM provider to be swapped via configuration
- The default provider configured in the repository is Anthropic Claude
- All tool calls must be scoped to the authenticated user's wallet
- The agent must handle cases where no data matches the query appropriately
- Conversation history must be maintained across turns within a session

---

## 9. API Surface (High Level)

```
POST   /auth/register              — create user account
POST   /auth/token                 — obtain JWT

GET    /wallet                     — get current user's wallet
POST   /wallet/deposit             — initiate deposit via gateway
POST   /wallet/withdraw            — initiate withdrawal via gateway
POST   /wallet/transfer            — transfer between users

GET    /transactions               — list transactions (paginated + filtered)
GET    /transactions/{id}          — transaction detail

POST   /webhooks/gateway           — receive gateway event (no auth, HMAC verification)

POST   /agent/chat                 — send message to agent
GET    /agent/sessions/{id}        — retrieve conversation history
```

---

## 10. Out of Scope

The following items are explicitly excluded from the challenge scope:

- Multi-currency conversion
- Chargeback / dispute flows
- KYC / identity verification
- Push notifications
- Admin dashboard
- Production deployment / Kubernetes