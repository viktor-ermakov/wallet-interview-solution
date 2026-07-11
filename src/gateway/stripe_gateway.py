"""
Real Stripe implementation of the PaymentGateway protocol (gateway/base.py).

Two things worth knowing if you haven't used Stripe's API before:

1. Stripe's Python SDK makes synchronous (blocking) HTTP calls. Since this
   whole app is async, calling it directly would block the entire event
   loop — every other request would freeze while waiting on Stripe. Each
   call below is wrapped in `asyncio.to_thread(...)`, which runs it on a
   background thread instead, so the event loop stays free.

2. Stripe amounts are integers in the currency's smallest unit (cents for
   BRL/USD), never decimals. `Decimal("10.00")` must become `1000`, not
   `10.00` — otherwise you either charge 100x too little or Stripe rejects
   the request outright.
"""

import asyncio
import uuid
from decimal import Decimal

import stripe

from src.config import settings
from src.gateway.base import PaymentIntent

stripe.api_key = settings.stripe_secret_key


def _to_cents(amount: Decimal) -> int:
    return int(amount * 100)


class StripeGateway:
    async def create_deposit_intent(self, amount: Decimal, currency: str, metadata: dict) -> PaymentIntent:
        intent = await asyncio.to_thread(
            stripe.PaymentIntent.create,
            amount=_to_cents(amount),
            currency=currency.lower(),  # Stripe requires lowercase ISO codes ("brl", not "BRL")
            metadata={k: str(v) for k, v in metadata.items() if v is not None},
            # This is an API-driven flow (no browser page for the customer to
            # be redirected back to), so we restrict Stripe to payment methods
            # that never need a redirect (e.g. cards) rather than supplying a
            # return_url for redirect-based methods (e.g. some bank/wallet
            # methods enabled by default in the Stripe dashboard).
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            # Idempotency key: protects against OUR OWN retries of this exact
            # call (e.g. a network blip causing our code to resend the same
            # request) being turned into duplicate charges by Stripe.
            #
            # Earlier version of this built the key from wallet_id+amount+
            # description — which seemed reasonable, but broke in practice:
            # two genuinely separate $20 deposits with the same description
            # produced the *same* key, so Stripe treated the second one as a
            # duplicate of the first and rejected it. A fresh uuid4 per call
            # avoids that collision entirely.
            #
            # Trade-off: this also means a true retry (the client re-sending
            # this exact HTTP request after a timeout) is no longer
            # deduplicated by Stripe — that would need the *caller* to send
            # a stable idempotency key on their own request (e.g. an
            # `Idempotency-Key` header), which BUSINESS_SPEC.md doesn't
            # currently require. Worth naming as a known limitation.
            idempotency_key=str(uuid.uuid4()),
        )
        return PaymentIntent(
            gateway_reference=intent.id,
            client_secret=intent.client_secret,
            status=intent.status,
        )

    async def create_payout(self, amount: Decimal, destination: dict, metadata: dict) -> PaymentIntent:
        # NOTE: real per-user payouts require Stripe Connect — the user's
        # wallet needs a linked, onboarded `stripe_connected_account_id`
        # (obtained through Stripe's Connect onboarding flow, which involves
        # its own KYC/identity-verification steps). That onboarding flow is
        # out of scope here; this assumes `destination` already carries a
        # valid connected account id, and documents that as a known
        # simplification rather than quietly faking full Connect support.
        connected_account_id = destination.get("stripe_connected_account_id")
        if not connected_account_id:
            raise ValueError(
                "destination must include 'stripe_connected_account_id' "
                "(requires the recipient to have completed Stripe Connect onboarding)"
            )

        transfer = await asyncio.to_thread(
            stripe.Transfer.create,
            amount=_to_cents(amount),
            currency="brl",
            destination=connected_account_id,
            metadata={k: str(v) for k, v in metadata.items() if v is not None},
        )
        return PaymentIntent(
            gateway_reference=transfer.id,
            client_secret=None,
            # Stripe Transfers succeed synchronously once created (unlike
            # PaymentIntents, which have their own pending/confirmed lifecycle).
            status="succeeded",
        )

    def verify_webhook(self, payload: bytes, signature: str) -> dict:
        """Verifies the request actually came from Stripe (D-05) using
        Stripe's official signed-payload check — this is what stops anyone
        from POSTing a fake "payment succeeded" event straight at our
        webhook endpoint to credit themselves money for free.

        Returns {"gateway_reference": None, "succeeded": None} for any
        Stripe event type we don't care about (Stripe sends many event
        types — customer.created, charge.dispute.created, etc. — most of
        which aren't deposit confirmations). The webhook handler treats that
        as "acknowledge, no action needed" rather than an error.
        """
        event = stripe.Webhook.construct_event(payload, signature, settings.stripe_webhook_secret)
        event_type = event["type"]
        obj = event["data"]["object"]

        if event_type == "payment_intent.succeeded":
            return {"gateway_reference": obj["id"], "succeeded": True}
        if event_type in ("payment_intent.payment_failed", "payment_intent.canceled"):
            return {"gateway_reference": obj["id"], "succeeded": False}

        return {"gateway_reference": None, "succeeded": None}