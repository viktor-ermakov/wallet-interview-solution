import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, Field

from src.wallet.models import TransactionStatus, TransactionType, WalletStatus


class WalletResponse(BaseModel):
    id: uuid.UUID
    balance: Decimal
    currency: str
    status: WalletStatus
    updated_at: datetime

    model_config = {"from_attributes": True}


class DepositRequest(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)
    currency: str = "BRL"
    description: str | None = None


class WithdrawRequest(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)
    destination: dict  # bank account or PIX key details
    description: str | None = None


class TransferRequest(BaseModel):
    amount: Decimal = Field(gt=0, decimal_places=2)
    recipient_email: str
    description: str | None = None


class TransactionResponse(BaseModel):
    id: uuid.UUID
    type: TransactionType
    amount: Decimal
    status: TransactionStatus
    description: str | None
    counterpart_transaction_id: uuid.UUID | None
    counterparty_email: str | None = None
    gateway_reference: str | None
    created_at: datetime

    model_config = {"from_attributes": True}


class TransactionListResponse(BaseModel):
    items: list[TransactionResponse]
    total: int
    page: int
    page_size: int