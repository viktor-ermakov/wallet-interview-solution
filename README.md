# Design Decisions

## Getting Started

Set up virtual environment:
```bash
Set-ExecutionPolicy -Scope Process -ExecutionPolicy RemoteSigned
python -m venv .venv
.\.venv\Scripts\Activate.ps1
```

```bash
cp .env.example .env        # fill in your API keys
docker compose up --build   # starts the API + PostgreSQL
```

The API will be available at `http://localhost:8000`.
Interactive documentation at `http://localhost:8000/docs`.

---

## Overview

This document walks through how the Digital Wallet described in `BUSINESS_SPEC.md` and
`TECHNICAL_SPEC.md` was actually built, and — just as importantly — where it was deliberately
left short of "production-ready" given the project's time budget. Nothing below is glossed
over: every trade-off is called out where it happens, rather than collected in a single
disclaimer at the end.

**What's fully working:** deposits (end-to-end, including real Stripe webhooks), transfers,
balance locking/concurrency, auth, transaction history, and the AI query agent.
**What's partial:** withdrawals (fully functional against `StubGateway`, blocked on Stripe
Connect for real payouts — see §1.5). Everything else out of scope is listed in §6.

If you just want the short version: **Stripe** was the gateway, **PostgreSQL row-locking**
handles concurrency, **LangChain + Claude** power the agent, and the three biggest
intentionally-incomplete pieces are Stripe Connect withdrawals, persistent agent sessions, and
Alembic migrations. Read on for the reasoning behind each.

### Table of contents

0. [Getting Started](#getting-started)
1. [Payment Gateway: Stripe](#1-payment-gateway-stripe)
2. [Wallet Domain Logic](#2-wallet-domain-logic)
3. [Auth](#3-auth)
4. [AI Agent](#4-ai-agent)
5. [Testing Strategy](#5-testing-strategy)
6. [Explicitly Out of Scope](#6-explicitly-out-of-scope)

---

## 1. Payment Gateway: Stripe

**Stripe** was chosen over Mercado Pago for two practical reasons: it has a mature
async-friendly test mode (test cards, a CLI for local webhook forwarding) that made real
end-to-end testing possible without a bank account, and `config.py`'s defaults were already
pointed at Stripe (`GATEWAY_PROVIDER=stripe`), suggesting it was the intended default.

Everything gateway-related sits behind the `PaymentGateway` protocol in `gateway/base.py`, so
a Mercado Pago implementation could be added later as a sibling class without touching
`wallet/service.py` — the service layer only ever depends on the protocol, never on Stripe
directly.

From there, four sub-decisions shaped the integration:

### 1.1 Keeping the event loop free: `asyncio.to_thread`

Stripe's Python SDK is synchronous, but the whole application is async. Calling it directly
from an `async def` would block the entire event loop on every Stripe call, stalling every
other in-flight request. Every call in `gateway/stripe_gateway.py` is wrapped in
`asyncio.to_thread(...)`, pushing it to a background thread instead.

### 1.2 A bug in the first idempotency key design — and the fix

The first version built the deposit idempotency key from `wallet_id + amount + description`.
That broke immediately in practice: two genuinely separate $20 deposits with the same
(default) description produced the *same* key, so Stripe treated the second as a duplicate and
rejected it with an `IdempotencyError`. The fix was a fresh `uuid4()` per call.

**Trade-off accepted:** this means a true network-level retry (the client re-sending the exact
same HTTP request after a timeout) is no longer deduplicated by Stripe on our side — that would
require the *caller* to supply their own stable `Idempotency-Key` header, which isn't in
`BUSINESS_SPEC.md`'s current scope. Worth adding if this project continued.

### 1.3 `StubGateway` and the silent fallback in `get_gateway()`

Before the real Stripe integration existed, `gateway/stub_gateway.py` was built as a
deliberately fake, in-memory `PaymentGateway` — every deposit/payout "succeeds" instantly, no
HTTP calls. `gateway/factory.py`'s `get_gateway()` falls back to `StubGateway` automatically
whenever `STRIPE_SECRET_KEY` isn't configured, regardless of `GATEWAY_PROVIDER`.

**Trade-off accepted:** this is a development convenience, not how you'd want it gated in
production. There, a missing credential should cause a hard startup failure, not a silent
downgrade to a fake gateway. Flagging this explicitly rather than presenting it as
production-ready behavior.

### 1.4 Webhook signature verification

`StripeGateway.verify_webhook` uses Stripe's official signed-payload verification
(`stripe.Webhook.construct_event`) — this is what stops anyone from POSTing a fake "payment
succeeded" event straight at the webhook endpoint to credit themselves money. It also filters
deposit-confirmation events (`payment_intent.succeeded`,
`payment_intent.payment_failed`/`canceled`) out from the many other event types a real Stripe
account sends (`customer.created`, etc.) — anything else is acknowledged with `200` and
silently ignored, rather than erroring, since erroring would make Stripe retry an event we
intentionally don't act on, forever.

`StubGateway.verify_webhook`, by contrast, does **no** real signature check — it just parses
the JSON body. This is documented in its own docstring and is not meant to be mistaken for a
security boundary.

### 1.5 Known limitation: withdrawals need Stripe Connect (not built)

Real per-user payouts on Stripe require the recipient to have an onboarded, connected account
via **Stripe Connect** — a separate flow involving identity verification and bank account
linking. That onboarding flow was out of scope given the time budget.
`StripeGateway.create_payout` assumes the wallet's `destination` dict already carries a valid
`stripe_connected_account_id`, and raises a clear `ValueError` if it's missing, rather than
silently failing or faking success.

**Bottom line:** deposits are the piece that's genuinely production-shaped end-to-end;
withdrawals work fully only against `StubGateway`.

---

## 2. Wallet Domain Logic

### 2.1 Avoiding transfer deadlocks: a fixed lock order

All balance mutations acquire a `SELECT ... FOR UPDATE` lock via
`WalletRepository.get_for_update` before touching `balance`. Single-wallet operations (deposit,
withdrawal) are straightforward — the interesting case is transfers, which lock **two** wallet
rows in the same transaction.

If wallets were always locked "sender first, then recipient," two simultaneous transfers in
opposite directions (A→B and B→A) could each grab one lock and wait forever for the other — a
classic deadlock. The fix: **always lock wallets in the same fixed order regardless of
sender/recipient role**, sorted by UUID string. Both transfers then agree on lock order, so one
simply waits briefly for the other instead of deadlocking.

### 2.2 Why deposits and withdrawals are asymmetric

This is easy to get wrong, so it's worth stating explicitly:

- **Deposit** — the balance is *not* touched when `/wallet/deposit` is called. A `PENDING`
  transaction is created and the gateway is asked to create a payment intent; the balance is
  only credited later, in `confirm_deposit`, once a webhook confirms the payment actually
  succeeded (D-02/D-03). This models the reality that deposit funds haven't arrived yet at
  request time.
- **Withdrawal** — the balance is debited *immediately* ("reserved") when `/wallet/withdraw`
  is called, before the gateway payout even runs (S-03). If the payout later fails, the
  reservation is reversed and the transaction marked `REVERSED` (S-04). This models the reality
  that once a withdrawal is requested, that money shouldn't be spendable again until the payout
  is confirmed to have failed.
- **Transfer** — fully synchronous and atomic. Both the debit and credit happen in the same
  database transaction, with no gateway involved at all (T-04), since it's purely internal.

### 2.3 How deposits stay idempotent under replayed webhooks

`Transaction.gateway_reference` has a `UNIQUE` database constraint, and `confirm_deposit`
additionally checks whether the transaction is already in a terminal state (`COMPLETED` /
`FAILED`) before mutating anything. Together these mean a replayed webhook event — which
payment gateways routinely send more than once — can never double-credit a wallet. This is
verified at two levels: a unit test (`test_confirm_deposit_is_idempotent`) and an integration
test that goes around the API to prove the database constraint itself holds
(`test_duplicate_gateway_reference_is_rejected`).

### 2.4 Transaction history: counterparty resolution and its cost

Per H-03, each transaction exposes a human-readable `counterparty_email` for transfers (not
just an opaque `counterpart_transaction_id`). Resolving this currently issues one extra query
per transaction in a list response — an N+1 pattern, acceptable at the page sizes this API
allows (≤100) but not something that would scale to very large pages. A production version
would batch-resolve all counterpart ids in a single query instead.

### 2.5 Why the service layer is testable without a database

`WalletService.__init__` accepts an optional `repo` parameter, falling back to constructing a
real `WalletRepository(session)` if none is given. This one change is what makes the entire
service layer testable with an in-memory fake repository, with zero database or network
dependency — directly satisfying the non-functional requirement that "domain logic must be
testable without infrastructure dependencies."

---

## 3. Auth

### 3.1 The JWT subject is the user's ID, not their email

`create_access_token`'s `sub` claim holds the user's UUID. This means a user changing their
email later doesn't invalidate their existing tokens — the token's identity claim never
changes underneath them.

### 3.2 Wallet creation is implicit in registration

Nothing in the API surface explicitly creates a wallet, so `register_user` creates one
automatically alongside the `User` row, in the same transaction. This was a deliberate reading
of W-01 ("each user may have exactly one active wallet"): without this, every wallet endpoint
would be permanently broken for any newly registered user.

### 3.3 Why auth failures are deliberately generic

Both "wrong password" and "no such user" return the identical `401 Incorrect email or
password`. This is deliberate — a more specific error would let an attacker enumerate which
emails are registered.

### 3.4 A known dependency pin: `bcrypt<4.1`

`passlib`'s bcrypt backend has a compatibility bug with `bcrypt` 4.1+ (it misreports "password
cannot be longer than 72 bytes" even on short passwords, due to a removed internal API
`passlib` still expects). `bcrypt<4.1` is pinned in `pyproject.toml` to avoid this — worth
revisiting if `passlib` ships a fix, since pinning below a package's latest major version is
itself a small piece of maintenance debt.

---

## 4. AI Agent

### 4.1 Data scoping is structural, not a prompt instruction

`build_tools(repo, user_id)` returns tools that each **close over** `user_id` from the outer
function's arguments. The LLM never sees or supplies a user id as a tool parameter — which
user's data a conversation can access is decided once, before the LLM is ever invoked, not
something the model is trusted to respect via a system-prompt instruction. This is what
actually enforces A-04, and it's verified directly in `test_agent_tools.py` by seeding two
different users' data into the same fake repository and asserting one user's tools never
return the other's rows.

### 4.2 Session storage is in-memory only

`agent/session.py`'s `InMemorySessionStore` holds conversations in a plain Python dict.

**Trade-off accepted:** all conversation history is lost on process restart, and this would not
work correctly if the API were ever scaled to multiple instances (a follow-up request routed to
a different instance wouldn't find the session). A production version would need this backed by
Redis or the database instead. Left as-is given the project's scope, but explicitly not
production-ready as written.

### 4.3 Provider-agnostic by construction

`agent/agent.py`'s `get_llm()` switches between `ChatAnthropic` and `ChatOpenAI` purely based
on `settings.llm_provider`, with no other code path depending on which one is active — meeting
the constraint that the LLM provider be swappable via configuration alone.

---

## 5. Testing Strategy

Three layers were built, matching `TECHNICAL_SPEC.md`'s testing table:

- **Unit tests** (`tests/unit/`) — `WalletService`, auth's pure functions
  (`hash_password`/`verify_password`/`create_access_token`), and the agent tools, all tested
  against in-memory fakes with zero database or network dependency. These run in well under a
  second.
- **Integration tests** (`tests/integration/`) — the deposit → webhook → balance flow, tested
  against a real PostgreSQL instance and real HTTP calls through the FastAPI app, with the
  gateway forced to `StubGateway` via a dependency override (so these tests don't depend on
  whatever Stripe credentials happen to be configured in a given developer's `.env`).
- **Manual/live verification** — the full flow including a real Stripe test-mode deposit (via
  the Stripe CLI forwarding real webhooks) and a live multi-turn conversation with the actual
  Claude-backed agent.

### 5.1 A real bug found and fixed in the test infrastructure

The integration test database engine was originally created once, at module import time.
`pytest-asyncio` creates a new event loop per test function by default, but `asyncpg`
connections are permanently bound to the event loop they were opened on — so a connection
pooled from one test would be reused by the next test running on an already-closed event loop,
corrupting in ways that surfaced as `'NoneType' object has no attribute 'send'` and `cannot
perform operation: another operation is in progress`. The fix was `poolclass=NullPool` on the
test engine, forcing a fresh connection on every checkout instead of reusing pooled ones across
tests.

### 5.2 Known gap: no test of a genuine bad webhook *signature*

`StubGateway.verify_webhook` does no real signature check, so
`test_webhook_rejects_invalid_signature` can only prove a malformed (non-JSON) body is rejected
— not that an incorrectly *signed* Stripe event is rejected. A real test of that would mock
`stripe.Webhook.construct_event` to raise `stripe.error.SignatureVerificationError` directly
against `StripeGateway`, and wasn't built given time constraints.

### 5.3 Known gap: `test_agent_chat.py` (integration) not implemented

The agent's actual behavior is covered at the unit level (`test_agent_tools.py`, scoping and
correctness of each tool) and was verified manually end-to-end against the real Claude API. A
full integration test — hitting `/agent/chat` over real HTTP with a stubbed LLM returning
deterministic tool calls — was left unimplemented; the skeleton in
`tests/integration/test_agent_chat.py` documents exactly what it should cover.

---

## 6. Explicitly Out of Scope

Carried over unchanged from `BUSINESS_SPEC.md` §10: multi-currency conversion,
chargeback/dispute flows, KYC/identity verification, push notifications, an admin dashboard,
and production deployment.

Given the project's actual time constraints, the following were also not completed:

- Mercado Pago gateway implementation (Stripe only)
- Stripe Connect onboarding (blocks real end-to-end withdrawals — see §1.5)
- Alembic migrations (schema is created via `Base.metadata.create_all()` at app startup — fine
  for development, but unlike a migration this can't express changes to *existing* tables, only
  create ones that don't yet exist)
- Persistent (non-in-memory) agent session storage (see §4.2)