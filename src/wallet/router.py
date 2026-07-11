import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from src.auth.models import User
from src.auth.service import get_current_user
from src.database import get_session
from src.gateway.base import PaymentGateway
from src.wallet.models import TransactionStatus, TransactionType
from src.wallet.schemas import (
    DepositRequest,
    TransactionListResponse,
    TransactionResponse,
    TransferRequest,
    WalletResponse,
    WithdrawRequest,
)
from src.wallet.service import InsufficientFundsError, WalletFrozenError, WalletNotFoundError, WalletService

router = APIRouter()


from src.gateway.factory import get_gateway


def get_wallet_service(
    session: AsyncSession = Depends(get_session),
    gateway: PaymentGateway = Depends(get_gateway),
) -> WalletService:
    return WalletService(session, gateway)


@router.get("/wallet", response_model=WalletResponse)
async def get_wallet(
    current_user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
):
    try:
        return await service.get_wallet(current_user.id)
    except WalletNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))


@router.post("/wallet/deposit", response_model=TransactionResponse, status_code=status.HTTP_202_ACCEPTED)
async def deposit(
    body: DepositRequest,
    current_user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
):
    try:
        return await service.deposit(current_user.id, body)
    except WalletNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except WalletFrozenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))


@router.post("/wallet/withdraw", response_model=TransactionResponse, status_code=status.HTTP_202_ACCEPTED)
async def withdraw(
    body: WithdrawRequest,
    current_user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
):
    try:
        return await service.withdraw(current_user.id, body)
    except WalletNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except WalletFrozenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InsufficientFundsError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))


@router.post("/wallet/transfer", response_model=TransactionResponse, status_code=status.HTTP_201_CREATED)
async def transfer(
    body: TransferRequest,
    current_user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
):
    try:
        debit_tx, _credit_tx = await service.transfer(current_user.id, body)
        # Returning the debit side: the transaction from the caller's own
        # point of view. The linked credit transaction is reachable via
        # debit_tx.counterpart_transaction_id if the client needs it.
        return debit_tx
    except WalletNotFoundError as e:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(e))
    except WalletFrozenError as e:
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(e))
    except InsufficientFundsError as e:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(e))
    except ValueError as e:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(e))


@router.get("/transactions", response_model=TransactionListResponse)
async def list_transactions(
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    type: TransactionType | None = Query(default=None),
    status_: TransactionStatus | None = Query(default=None, alias="status"),
    start_date: datetime | None = Query(default=None),
    end_date: datetime | None = Query(default=None),
    min_amount: Decimal | None = Query(default=None, ge=0),
    max_amount: Decimal | None = Query(default=None, ge=0),
    current_user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
):
    wallet = await service.get_wallet(current_user.id)
    items, total = await service.repo.list_transactions(
        wallet.id,
        page=page,
        page_size=page_size,
        type=type,
        status=status_,
        start_date=start_date,
        end_date=end_date,
        min_amount=min_amount,
        max_amount=max_amount,
    )

    # NOTE: this issues one extra query per transaction to resolve its
    # counterparty (N+1 pattern). Fine at page_size<=100 for this exercise;
    # a production version would batch-resolve all counterpart_transaction_ids
    # in a single query instead.
    responses = []
    for tx in items:
        response = TransactionResponse.model_validate(tx)
        response.counterparty_email = await service.repo.get_counterparty_email(tx)
        responses.append(response)

    return TransactionListResponse(items=responses, total=total, page=page, page_size=page_size)


@router.get("/transactions/{transaction_id}", response_model=TransactionResponse)
async def get_transaction(
    transaction_id: uuid.UUID,
    current_user: User = Depends(get_current_user),
    service: WalletService = Depends(get_wallet_service),
):
    wallet = await service.get_wallet(current_user.id)
    tx = await service.repo.get_transaction(transaction_id, wallet.id)
    if tx is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Transaction not found")

    response = TransactionResponse.model_validate(tx)
    response.counterparty_email = await service.repo.get_counterparty_email(tx)
    return response