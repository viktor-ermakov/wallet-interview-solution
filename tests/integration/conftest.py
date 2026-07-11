import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import NullPool

from src.database import Base, get_session
from src.main import app

# Requires a running PostgreSQL instance. Start one with:
#   docker run -e POSTGRES_USER=wallet -e POSTGRES_PASSWORD=wallet \
#              -e POSTGRES_DB=wallet_test -p 5433:5432 postgres:15
TEST_DATABASE_URL = "postgresql+asyncpg://wallet:wallet@localhost:5433/wallet_test"

# NullPool: pytest-asyncio opens a NEW event loop per test function by
# default, but asyncpg connections are permanently bound to the loop they
# were created on. A normal connection pool holds connections open across
# tests, so test 2 (running on a fresh loop) would try to reuse a connection
# opened on test 1's already-closed loop — that's exactly what produced
# "cannot perform operation: another operation is in progress" and
# "'NoneType' object has no attribute 'send'". NullPool opens a brand new
# connection on every checkout instead of reusing one, which costs a little
# speed but avoids this cross-event-loop corruption entirely.
_engine = create_async_engine(TEST_DATABASE_URL, poolclass=NullPool)
_TestSession = async_sessionmaker(_engine, expire_on_commit=False)


@pytest_asyncio.fixture(autouse=True)
async def reset_db():
    async with _engine.begin() as conn:
        await conn.run_sync(Base.metadata.drop_all)
        await conn.run_sync(Base.metadata.create_all)
    yield


@pytest_asyncio.fixture
async def db_session() -> AsyncSession:
    async with _TestSession() as s:
        yield s


@pytest_asyncio.fixture
async def client(db_session: AsyncSession) -> AsyncClient:
    async def _override():
        yield db_session

    app.dependency_overrides[get_session] = _override
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c
    app.dependency_overrides.clear()