from contextlib import asynccontextmanager

from fastapi import FastAPI

from src.auth.router import router as auth_router
from src.gateway.webhook_handler import router as webhook_router
from src.wallet.router import router as wallet_router
from src.agent.router import router as agent_router

# Importing these registers their tables on Base.metadata — required before
# create_all() below can know these tables exist at all.
import src.auth.models  # noqa: F401
import src.wallet.models  # noqa: F401
from src.database import Base, engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    # NOTE: create_all() is a fast way to get a working schema during
    # development/interview timelines. It is NOT how you'd do this in a real
    # production system — Alembic migrations (already in the chosen stack
    # per TECHNICAL_SPEC.md) are the correct long-term approach, since
    # create_all() can't express schema *changes* to existing tables, only
    # create ones that don't exist yet. Worth calling out as a known
    # trade-off in the README's design-decisions section.
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield


def create_app() -> FastAPI:
    app = FastAPI(
        title="Digital Wallet API",
        version="0.1.0",
        description="Digital Wallet with payment gateway integration and AI insights agent.",
        lifespan=lifespan,
    )

    app.include_router(auth_router, prefix="/auth", tags=["auth"])
    app.include_router(wallet_router, tags=["wallet"])
    app.include_router(webhook_router, tags=["webhooks"])
    app.include_router(agent_router, prefix="/agent", tags=["agent"])

    return app


app = create_app()