import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy.ext.asyncio import AsyncSession

from src.gateway.base import PaymentGateway
from src.wallet.models import Transaction, TransactionStatus, TransactionType, Wallet, WalletStatus
from src.wallet.repository import WalletRepository
from src.wallet.schemas import DepositRequest, TransferRequest, WithdrawRequest


class InsufficientFundsError(Exception):
    pass


class WalletNotFoundError(Exception):
    pass


class WalletFrozenError(Exception):
    pass


class TransactionNotFoundError(Exception):
    pass


class WalletService:
    def __init__(self, session: AsyncSession, gateway: PaymentGateway, repo: WalletRepository | None = None):
        # `repo` is injectable so tests can pass a fake, in-memory repository
        # instead of a real one backed by a database session. Production code
        # (the router) never passes `repo`, so behavior there is unchanged.
        self.repo = repo if repo is not None else WalletRepository(session)
        self.session = session
        self.gateway = gateway

    async def get_wallet(self, user_id: uuid.UUID) -> Wallet:
        wallet = await self.repo.get_by_user_id(user_id)
        if not wallet:
            raise WalletNotFoundError(f"No wallet found for user {user_id}")
        return wallet

    async def deposit(self, user_id: uuid.UUID, request: DepositRequest) -> Transaction:
        """D-01/D-02: start a deposit. Balance is NOT touched here — only
        `confirm_deposit` (called from the webhook handler) credits the wallet,
        once the gateway actually confirms the money arrived (D-03/D-04).
        """
        wallet = await self.get_wallet(user_id)
        self._assert_active(wallet)

        intent = await self.gateway.create_deposit_intent(
            amount=request.amount,
            currency=request.currency,
            metadata={"wallet_id": str(wallet.id), "description": request.description},
        )

        transaction = await self.repo.create_transaction(
            wallet_id=wallet.id,
            type=TransactionType.DEPOSIT,
            amount=request.amount,
            balance_before=wallet.balance,
            balance_after=wallet.balance,  # unchanged until the webhook confirms
            status=TransactionStatus.PENDING,
            description=request.description,
            gateway_reference=intent.gateway_reference,
            extra_data={"client_secret": intent.client_secret, "gateway_status": intent.status},
        )
        await self.session.commit()
        return transaction

    async def confirm_deposit(self, gateway_reference: str, succeeded: bool) -> Transaction:
        """Called by the gateway webhook handler once payment is confirmed
        or rejected. This is where D-03/D-04 and the idempotency rule (D-05,
        §4.4) actually live.
        """
        transaction = await self.repo.get_by_gateway_reference(gateway_reference)
        if transaction is None:
            raise WalletNotFoundError(f"No transaction found for gateway_reference={gateway_reference}")

        # Idempotency: a terminal-state transaction must never be mutated again,
        # even if the gateway resends the same webhook event.
        if transaction.status in (TransactionStatus.COMPLETED, TransactionStatus.FAILED):
            return transaction

        wallet = await self.repo.get_for_update(transaction.wallet_id)

        if succeeded:
            wallet.balance += transaction.amount
            transaction.balance_after = wallet.balance
            transaction.status = TransactionStatus.COMPLETED
        else:
            transaction.status = TransactionStatus.FAILED
            # balance_after already equals balance_before from creation time —
            # nothing to change, per D-04 ("the balance is never changed").

        await self.session.commit()
        return transaction

    async def withdraw(self, user_id: uuid.UUID, request: WithdrawRequest) -> Transaction:
        """S-01..S-04: debit (reserve) the balance immediately, then ask the
        gateway to pay out. If the gateway call fails, the reservation is
        reversed. The DB lock is released between the debit and the gateway
        call on purpose — you never want to hold a row lock during a slow
        network call to an external service.
        """
        wallet_ref = await self.get_wallet(user_id)
        wallet = await self.repo.get_for_update(wallet_ref.id)
        self._assert_active(wallet)

        if wallet.balance < request.amount:
            raise InsufficientFundsError(
                f"Wallet {wallet.id} has insufficient balance for withdrawal of {request.amount}"
            )

        balance_before = wallet.balance
        wallet.balance -= request.amount  # reserve now, per S-03

        transaction = await self.repo.create_transaction(
            wallet_id=wallet.id,
            type=TransactionType.WITHDRAWAL,
            amount=request.amount,
            balance_before=balance_before,
            balance_after=wallet.balance,
            status=TransactionStatus.PENDING,
            description=request.description,
            extra_data={"destination": request.destination},
        )
        await self.session.commit()  # releases the row lock

        try:
            intent = await self.gateway.create_payout(
                amount=request.amount,
                destination=request.destination,
                metadata={"transaction_id": str(transaction.id)},
            )
        except Exception:
            await self._reverse_withdrawal(transaction.id)
            raise

        transaction.gateway_reference = intent.gateway_reference
        if intent.status == "succeeded":
            transaction.status = TransactionStatus.COMPLETED
        elif intent.status == "failed":
            await self._reverse_withdrawal(transaction.id)
        # otherwise leave PENDING — final state arrives later via webhook,
        # same as the deposit flow.
        await self.session.commit()
        return transaction

    async def _reverse_withdrawal(self, transaction_id: uuid.UUID) -> None:
        """S-04: gateway payout failed — give the reserved balance back."""
        transaction = await self.repo.get_transaction_by_id(transaction_id)
        wallet = await self.repo.get_for_update(transaction.wallet_id)
        wallet.balance += transaction.amount
        transaction.balance_after = wallet.balance
        transaction.status = TransactionStatus.REVERSED
        await self.session.commit()

    async def transfer(self, sender_user_id: uuid.UUID, request: TransferRequest) -> tuple[Transaction, Transaction]:
        """T-01..T-05: internal, atomic, two linked rows, no gateway call.

        The tricky part is lock ordering (TECHNICAL_SPEC.md §4.1). If sender
        and recipient wallets were always locked "sender first, then recipient",
        two simultaneous transfers in opposite directions (A→B and B→A) could
        each hold one lock and wait forever for the other — a deadlock. The
        fix: always lock wallets in the same fixed order regardless of role,
        e.g. sorted by UUID.
        """
        sender_ref = await self.get_wallet(sender_user_id)
        recipient_ref = await self.repo.get_by_email(request.recipient_email)
        if recipient_ref is None:
            raise WalletNotFoundError(f"No wallet found for recipient {request.recipient_email}")
        if recipient_ref.user_id == sender_user_id:
            raise ValueError("Cannot transfer to your own wallet")

        first_id, second_id = sorted([sender_ref.id, recipient_ref.id], key=str)
        first_wallet = await self.repo.get_for_update(first_id)
        second_wallet = await self.repo.get_for_update(second_id)

        sender_wallet = first_wallet if first_wallet.id == sender_ref.id else second_wallet
        recipient_wallet = second_wallet if sender_wallet is first_wallet else first_wallet

        self._assert_active(sender_wallet)
        self._assert_active(recipient_wallet)

        if sender_wallet.balance < request.amount:
            raise InsufficientFundsError(
                f"Wallet {sender_wallet.id} has insufficient balance for transfer of {request.amount}"
            )

        sender_before = sender_wallet.balance
        recipient_before = recipient_wallet.balance
        sender_wallet.balance -= request.amount
        recipient_wallet.balance += request.amount

        debit_tx = await self.repo.create_transaction(
            wallet_id=sender_wallet.id,
            type=TransactionType.TRANSFER_DEBIT,
            amount=request.amount,
            balance_before=sender_before,
            balance_after=sender_wallet.balance,
            status=TransactionStatus.COMPLETED,
            description=request.description,
        )
        credit_tx = await self.repo.create_transaction(
            wallet_id=recipient_wallet.id,
            type=TransactionType.TRANSFER_CREDIT,
            amount=request.amount,
            balance_before=recipient_before,
            balance_after=recipient_wallet.balance,
            status=TransactionStatus.COMPLETED,
            description=request.description,
            counterpart_transaction_id=debit_tx.id,
        )
        debit_tx.counterpart_transaction_id = credit_tx.id

        await self.session.commit()
        return debit_tx, credit_tx

    async def list_transactions(
        self,
        user_id: uuid.UUID,
        *,
        page: int = 1,
        page_size: int = 20,
        type: TransactionType | None = None,
        status: TransactionStatus | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        min_amount: Decimal | None = None,
        max_amount: Decimal | None = None,
    ) -> tuple[list[Transaction], int]:
        """H-01/H-02: list the caller's own transactions, paginated and
        filtered. Scoping happens by resolving the wallet from `user_id`
        first — the caller has no way to pass someone else's wallet_id in.
        """
        wallet = await self.get_wallet(user_id)
        return await self.repo.list_transactions(
            wallet.id,
            page=page,
            page_size=page_size,
            type=type,
            status=status,
            start_date=start_date,
            end_date=end_date,
            min_amount=min_amount,
            max_amount=max_amount,
        )

    async def get_transaction(self, user_id: uuid.UUID, transaction_id: uuid.UUID) -> Transaction:
        """H-03: fetch a single transaction, scoped to the caller's own
        wallet — a user can never read another user's transaction by ID.
        """
        wallet = await self.get_wallet(user_id)
        transaction = await self.repo.get_transaction(transaction_id, wallet.id)
        if transaction is None:
            raise TransactionNotFoundError(f"No transaction {transaction_id} for this wallet")
        return transaction

    def _assert_active(self, wallet: Wallet) -> None:
        if wallet.status == WalletStatus.FROZEN:
            raise WalletFrozenError("Wallet is frozen")
        if wallet.status == WalletStatus.CLOSED:
            raise WalletFrozenError("Wallet is closed")