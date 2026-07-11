"""
Single source of truth for "which PaymentGateway instance do we use right
now" — both wallet/router.py and gateway/webhook_handler.py import this,
so they can never disagree about which gateway is active.
"""

from src.config import settings
from src.gateway.base import PaymentGateway


def get_gateway() -> PaymentGateway:
    # Falls back to StubGateway whenever no real Stripe secret key is
    # configured, regardless of GATEWAY_PROVIDER — this keeps local
    # development/testing working without needing real Stripe test
    # credentials in every contributor's .env. Not how you'd gate this in
    # production (there you'd want a hard failure, not a silent fallback,
    # if credentials are missing) — flagged here as a deliberate dev-time
    # convenience, worth calling out in the README.
    if settings.gateway_provider == "stripe" and settings.stripe_secret_key:
        from src.gateway.stripe_gateway import StripeGateway

        return StripeGateway()

    if settings.gateway_provider == "mercadopago":
        raise NotImplementedError(
            "Mercado Pago gateway not implemented yet — set GATEWAY_PROVIDER=stripe "
            "or implement gateway/mercadopago_gateway.py"
        )

    from src.gateway.stub_gateway import StubGateway

    return StubGateway()