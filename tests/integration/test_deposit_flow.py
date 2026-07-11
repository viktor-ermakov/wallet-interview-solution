"""
Integration tests for the deposit → webhook → balance flow.

These tests hit a real PostgreSQL instance (managed by conftest.py).
The gateway is stubbed so no real payment provider is called.
"""
from decimal import Decimal

import pytest
import pytest_asyncio
from httpx import AsyncClient
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.factory import get_gateway
from src.gateway.stub_gateway import StubGateway
from src.main import app
from src.wallet.repository import WalletRepository


@pytest_asyncio.fixture(autouse=True)
async def force_stub_gateway():
    """Forces StubGateway regardless of what GATEWAY_PROVIDER/STRIPE_SECRET_KEY
    happen to be set to in .env. Without this, these tests' behavior would
    depend on whatever gateway credentials the person running them happens
    to have configured locally — sometimes hitting real Stripe, sometimes
    not, which is exactly the kind of nondeterminism a test suite shouldn't
    have. This overrides the same get_gateway dependency both
    wallet/router.py and gateway/webhook_handler.py use, so it covers both.
    """
    app.dependency_overrides[get_gateway] = lambda: StubGateway()
    yield
    app.dependency_overrides.pop(get_gateway, None)


async def _register_and_login(client: AsyncClient, email: str) -> str:
    await client.post(
        "/auth/register",
        json={"email": email, "name": "Test User", "password": "supersecret123"},
    )
    response = await client.post(
        "/auth/token",
        data={"username": email, "password": "supersecret123"},
    )
    return response.json()["access_token"]


class TestDepositWebhookFlow:
    async def test_webhook_credits_balance_on_payment_success(self, client: AsyncClient):
        token = await _register_and_login(client, "deposit-success@example.com")
        headers = {"Authorization": f"Bearer {token}"}

        deposit_resp = await client.post("/wallet/deposit", json={"amount": 50.00}, headers=headers)
        assert deposit_resp.status_code == 202
        gateway_reference = deposit_resp.json()["gateway_reference"]
        assert gateway_reference is not None

        webhook_resp = await client.post(
            "/webhooks/gateway",
            json={"gateway_reference": gateway_reference, "succeeded": True},
        )
        assert webhook_resp.status_code == 200

        wallet_resp = await client.get("/wallet", headers=headers)
        assert wallet_resp.json()["balance"] == "50.00"

        transactions_resp = await client.get("/transactions", headers=headers)
        matching = [t for t in transactions_resp.json()["items"] if t["gateway_reference"] == gateway_reference]
        assert len(matching) == 1
        assert matching[0]["status"] == "COMPLETED"

    async def test_webhook_is_idempotent(self, client: AsyncClient):
        token = await _register_and_login(client, "deposit-idempotent@example.com")
        headers = {"Authorization": f"Bearer {token}"}

        deposit_resp = await client.post("/wallet/deposit", json={"amount": 30.00}, headers=headers)
        gateway_reference = deposit_resp.json()["gateway_reference"]

        webhook_body = {"gateway_reference": gateway_reference, "succeeded": True}
        first = await client.post("/webhooks/gateway", json=webhook_body)
        second = await client.post("/webhooks/gateway", json=webhook_body)  # replay

        assert first.status_code == 200
        assert second.status_code == 200  # replays are acknowledged, not errors

        wallet_resp = await client.get("/wallet", headers=headers)
        assert wallet_resp.json()["balance"] == "30.00"  # not 60.00

    async def test_duplicate_gateway_reference_is_rejected(self, client: AsyncClient, db_session: AsyncSession):
        # The API itself can never produce a duplicate gateway_reference
        # (StubGateway generates a fresh uuid4 per call), so this test goes
        # around the API and exercises the database constraint directly —
        # this is the schema-level guarantee that D-05's idempotency
        # promise ultimately rests on.
        token = await _register_and_login(client, "dup-ref@example.com")
        wallet_resp = await client.get("/wallet", headers={"Authorization": f"Bearer {token}"})
        wallet_id = wallet_resp.json()["id"]

        repo = WalletRepository(db_session)
        await repo.create_transaction(
            wallet_id=wallet_id,
            type="DEPOSIT",
            amount=Decimal("10.00"),
            balance_before=Decimal("0.00"),
            balance_after=Decimal("0.00"),
            status="PENDING",
            gateway_reference="dup-ref-123",
        )
        await db_session.commit()

        with pytest.raises(IntegrityError):
            await repo.create_transaction(
                wallet_id=wallet_id,
                type="DEPOSIT",
                amount=Decimal("20.00"),
                balance_before=Decimal("0.00"),
                balance_after=Decimal("0.00"),
                status="PENDING",
                gateway_reference="dup-ref-123",  # same reference — violates the UNIQUE constraint
            )

    async def test_webhook_rejects_invalid_signature(self, client: AsyncClient):
        # StubGateway.verify_webhook does no real signature check (it's a
        # dev-only stand-in — see its docstring) — the one thing it CAN
        # reject is a body that isn't valid JSON at all, which exercises the
        # same error-handling path a real bad-signature rejection would.
        # A real HMAC-signature rejection test belongs against
        # StripeGateway.verify_webhook specifically (mocking
        # stripe.Webhook.construct_event to raise), not this Postgres-backed
        # integration test — worth adding as a separate unit test.
        response = await client.post(
            "/webhooks/gateway",
            content=b"this is not valid json",
            headers={"Content-Type": "application/json"},
        )
        assert response.status_code == 400