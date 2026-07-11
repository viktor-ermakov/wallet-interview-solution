import uuid
from decimal import Decimal

from src.gateway.base import PaymentIntent


class StubGateway:
    """TEMPORARY. Stands in for a real payment gateway (Stripe/Mercado Pago)
    so /wallet/deposit and /wallet/withdraw can be exercised end-to-end before
    the real integration exists. No real HTTP calls are made and no real
    money moves — every deposit intent and every payout instantly "succeeds".

    Replace this with a real <provider>_gateway.py (implementing the same
    PaymentGateway protocol from gateway/base.py) once you've picked Stripe
    or Mercado Pago, and swap it in inside wallet/router.py's get_gateway().
    """

    async def create_deposit_intent(self, amount: Decimal, currency: str, metadata: dict) -> PaymentIntent:
        return PaymentIntent(
            gateway_reference=f"stub_dep_{uuid.uuid4().hex[:10]}",
            client_secret="stub_secret",
            status="requires_confirmation",
        )

    async def create_payout(self, amount: Decimal, destination: dict, metadata: dict) -> PaymentIntent:
        return PaymentIntent(
            gateway_reference=f"stub_payout_{uuid.uuid4().hex[:10]}",
            client_secret=None,
            status="succeeded",
        )

    def verify_webhook(self, payload: bytes, signature: str) -> dict:
        """TEMPORARY: does NOT check any real signature — just parses the
        JSON body as-is. A real gateway implementation MUST verify an HMAC
        signature here (per D-05 / TECHNICAL_SPEC.md §4.4); skipping that
        check would let anyone credit arbitrary deposits by hitting the
        webhook endpoint directly. This exists only so the webhook flow can
        be exercised by hand (e.g. via Swagger UI) before Stripe/Mercado
        Pago's real signed-webhook verification is wired up.
        """
        import json

        return json.loads(payload)