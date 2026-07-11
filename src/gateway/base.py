from dataclasses import dataclass
from decimal import Decimal
from typing import Protocol


@dataclass
class PaymentIntent:
    gateway_reference: str
    client_secret: str | None
    status: str


class PaymentGateway(Protocol):
    async def create_deposit_intent(
        self,
        amount: Decimal,
        currency: str,
        metadata: dict,
    ) -> PaymentIntent: ...

    async def create_payout(
        self,
        amount: Decimal,
        destination: dict,
        metadata: dict,
    ) -> PaymentIntent: ...

    def verify_webhook(self, payload: bytes, signature: str) -> dict: ...
