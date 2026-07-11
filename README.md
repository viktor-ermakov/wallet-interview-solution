# Wallet Interview Challenge

Technical interview project. Build a **Digital Wallet** in Python with:

- REST API (FastAPI + PostgreSQL)
- Payment gateway integration (Stripe or Mercado Pago)
- AI agent for natural language queries (Anthropic Claude with tool use)

---

## Read before you start

| Document | Content |
|---|---|
| [docs/BUSINESS_SPEC.md](docs/BUSINESS_SPEC.md) | What to build — domain rules, functional requirements, agent specification |
| [docs/TECHNICAL_SPEC.md](docs/TECHNICAL_SPEC.md) | Suggested stack, project structure, design patterns |

---

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

## What's already here

The repository includes:

- `src/` — skeleton with models, schemas, and routes. **All business logic is `raise NotImplementedError`** — your task is to implement it.
- `tests/` — test structure with named cases to guide coverage.
- `pyproject.toml`, `Dockerfile`, `docker-compose.yml` — ready to use.

---

## Deliverables

1. **Working implementation** that runs with `docker compose up`
2. **Tests** — unit tests for the domain logic (no DB) and at least one end-to-end integration test
3. **README section** explaining the design choices made (gateway chosen, architecture decisions, trade-offs)

---

## Estimated Time

4–8 hours. Prioritize correctness and clarity over completeness. A well-designed partial implementation is worth more than a rushed complete one.

---

## Constraints

- Python 3.11+
- Must run via Docker — no host dependencies
- Use LangChain for the agent — the LLM provider must be swappable via configuration
- No frontend — API only