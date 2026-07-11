"""
Handles inbound events from the payment gateway. No JWT auth here — the
gateway is not a logged-in user (BUSINESS_SPEC.md §9 lists this route with
no auth requirement) — trust instead comes from verifying the payload's
signature (D-05).
"""

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.database import get_session
from src.gateway.base import PaymentGateway
from src.gateway.factory import get_gateway
from src.wallet.service import WalletNotFoundError, WalletService

router = APIRouter()


@router.post("/webhooks/gateway", status_code=status.HTTP_200_OK)
async def gateway_webhook(
    request: Request,
    session: AsyncSession = Depends(get_session),
    gateway: PaymentGateway = Depends(get_gateway),
):
    payload = await request.body()
    signature = request.headers.get("X-Signature") or request.headers.get("Stripe-Signature", "")

    try:
        event = gateway.verify_webhook(payload, signature)
    except Exception:
        # Bad/missing signature, or a body that isn't even valid JSON —
        # either way, this isn't a request we can trust.
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail="Invalid webhook payload")

    gateway_reference = event.get("gateway_reference")
    succeeded = event.get("succeeded")
    if gateway_reference is None or succeeded is None:
        # A verified-but-irrelevant event (e.g. a Stripe event type that
        # isn't a deposit confirmation) looks identical to this at this
        # point. Acknowledge with 200 rather than erroring — there's
        # nothing actionable here, and erroring would make Stripe retry
        # an event we're intentionally ignoring, forever.
        return {"received": True}

    service = WalletService(session, gateway)
    try:
        await service.confirm_deposit(gateway_reference, succeeded=bool(succeeded))
    except WalletNotFoundError:
        # Unknown gateway_reference: acknowledge anyway so the gateway
        # doesn't retry forever — in a real system this would be logged
        # for investigation rather than silently passed.
        pass

    return {"received": True}