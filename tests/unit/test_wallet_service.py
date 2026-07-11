"""
Unit tests for WalletService.

These tests must NOT use a real database or make real HTTP calls.
Use fakes/stubs for WalletRepository and PaymentGateway.
"""
import uuid
from dataclasses import dataclass
from decimal import Decimal

import pytest

from src.wallet.models import Transaction, TransactionStatus, TransactionType, Wallet, WalletStatus
from src.wallet.schemas import DepositRequest, TransferRequest, WithdrawRequest
from src.wallet.service import InsufficientFundsError, WalletFrozenError, WalletNotFoundError, WalletService

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class FakeSession:
    """WalletService calls session.commit()/flush(); fakes don't need a real DB
    transaction since FakeWalletRepository mutates objects directly in memory."""

    async def commit(self):
        pass

    async def flush(self):
        pass


class FakeWalletRepository:
    """In-memory replacement for WalletRepository."""

    def __init__(self, wallets: dict[uuid.UUID, Wallet] | None = None):
        self.wallets = wallets or {}
        self.wallets_by_user = {w.user_id: w for w in self.wallets.values()}
        self.wallets_by_email: dict[str, Wallet] = {}
        self.transactions: dict[uuid.UUID, Transaction] = {}

    def register_email(self, email: str, wallet: Wallet):
        self.wallets_by_email[email] = wallet

    async def get_by_user_id(self, user_id):
        return self.wallets_by_user.get(user_id)

    async def get_by_email(self, email):
        return self.wallets_by_email.get(email)

    async def get_for_update(self, wallet_id):
        return self.wallets.get(wallet_id)

    async def create_transaction(self, **kwargs):
        tx = Transaction(id=uuid.uuid4(), created_at=None, **kwargs)
        self.transactions[tx.id] = tx
        return tx

    async def get_transaction(self, transaction_id, wallet_id):
        tx = self.transactions.get(transaction_id)
        return tx if tx and tx.wallet_id == wallet_id else None

    async def get_transaction_by_id(self, transaction_id):
        return self.transactions.get(transaction_id)

    async def get_by_gateway_reference(self, gateway_reference):
        for tx in self.transactions.values():
            if tx.gateway_reference == gateway_reference:
                return tx
        return None

    async def list_transactions(self, wallet_id, *, page=1, page_size=20, type=None, status=None):
        items = [t for t in self.transactions.values() if t.wallet_id == wallet_id]
        return items, len(items)


@dataclass
class FakeGateway:
    """Stub PaymentGateway that returns predictable results without HTTP calls."""

    next_payout_status: str = "succeeded"
    raise_on_payout: bool = False

    async def create_deposit_intent(self, amount, currency, metadata):
        from src.gateway.base import PaymentIntent
        return PaymentIntent(
            gateway_reference=f"dep_{uuid.uuid4().hex[:8]}",
            client_secret="secret_123",
            status="requires_confirmation",
        )

    async def create_payout(self, amount, destination, metadata):
        if self.raise_on_payout:
            raise ConnectionError("simulated gateway outage")
        from src.gateway.base import PaymentIntent
        return PaymentIntent(
            gateway_reference=f"payout_{uuid.uuid4().hex[:8]}",
            client_secret=None,
            status=self.next_payout_status,
        )

    def verify_webhook(self, payload, signature):
        raise NotImplementedError


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_wallet(balance="100.00", status=WalletStatus.ACTIVE, currency="BRL") -> Wallet:
    return Wallet(id=uuid.uuid4(), user_id=uuid.uuid4(), balance=Decimal(balance), currency=currency, status=status)


def make_service(repo: FakeWalletRepository, gateway: FakeGateway | None = None) -> WalletService:
    return WalletService(session=FakeSession(), gateway=gateway or FakeGateway(), repo=repo)


# ---------------------------------------------------------------------------
# Deposit tests
# ---------------------------------------------------------------------------

class TestDeposit:
    async def test_creates_pending_transaction_with_gateway_reference(self):
        wallet = make_wallet(balance="50.00")
        repo = FakeWalletRepository({wallet.id: wallet})
        service = make_service(repo)

        tx = await service.deposit(wallet.user_id, DepositRequest(amount=Decimal("20.00")))

        assert tx.status == TransactionStatus.PENDING
        assert wallet.balance == Decimal("50.00")  # unchanged until webhook confirms
        assert tx.gateway_reference is not None

    async def test_raises_when_wallet_frozen(self):
        wallet = make_wallet(status=WalletStatus.FROZEN)
        repo = FakeWalletRepository({wallet.id: wallet})
        service = make_service(repo)

        with pytest.raises(WalletFrozenError):
            await service.deposit(wallet.user_id, DepositRequest(amount=Decimal("20.00")))

    async def test_raises_when_wallet_not_found(self):
        repo = FakeWalletRepository({})
        service = make_service(repo)

        with pytest.raises(WalletNotFoundError):
            await service.deposit(uuid.uuid4(), DepositRequest(amount=Decimal("20.00")))


# ---------------------------------------------------------------------------
# Withdrawal tests
# ---------------------------------------------------------------------------

class TestWithdraw:
    async def test_debits_balance_before_gateway_call(self):
        wallet = make_wallet(balance="100.00")
        repo = FakeWalletRepository({wallet.id: wallet})
        gateway = FakeGateway(next_payout_status="pending")
        service = make_service(repo, gateway)

        tx = await service.withdraw(
            wallet.user_id, WithdrawRequest(amount=Decimal("30.00"), destination={"pix": "x"})
        )

        assert wallet.balance == Decimal("70.00")  # reserved immediately, before/regardless of gateway result
        assert tx.status == TransactionStatus.PENDING

    async def test_raises_on_insufficient_funds(self):
        wallet = make_wallet(balance="10.00")
        repo = FakeWalletRepository({wallet.id: wallet})
        service = make_service(repo)

        with pytest.raises(InsufficientFundsError):
            await service.withdraw(
                wallet.user_id, WithdrawRequest(amount=Decimal("30.00"), destination={"pix": "x"})
            )
        assert wallet.balance == Decimal("10.00")

    async def test_releases_reservation_on_gateway_failure(self):
        wallet = make_wallet(balance="100.00")
        repo = FakeWalletRepository({wallet.id: wallet})
        gateway = FakeGateway(raise_on_payout=True)
        service = make_service(repo, gateway)

        with pytest.raises(ConnectionError):
            await service.withdraw(
                wallet.user_id, WithdrawRequest(amount=Decimal("30.00"), destination={"pix": "x"})
            )

        assert wallet.balance == Decimal("100.00")  # reservation released
        tx = next(iter(repo.transactions.values()))
        assert tx.status == TransactionStatus.REVERSED


# ---------------------------------------------------------------------------
# Transfer tests
# ---------------------------------------------------------------------------

class TestTransfer:
    async def test_debit_and_credit_are_linked(self):
        sender = make_wallet(balance="100.00")
        recipient = make_wallet(balance="20.00")
        repo = FakeWalletRepository({sender.id: sender, recipient.id: recipient})
        repo.register_email("bob@example.com", recipient)
        service = make_service(repo)

        debit_tx, credit_tx = await service.transfer(
            sender.user_id, TransferRequest(amount=Decimal("40.00"), recipient_email="bob@example.com")
        )

        assert debit_tx.counterpart_transaction_id == credit_tx.id
        assert credit_tx.counterpart_transaction_id == debit_tx.id

    async def test_total_supply_is_conserved(self):
        sender = make_wallet(balance="100.00")
        recipient = make_wallet(balance="20.00")
        total_before = sender.balance + recipient.balance
        repo = FakeWalletRepository({sender.id: sender, recipient.id: recipient})
        repo.register_email("bob@example.com", recipient)
        service = make_service(repo)

        await service.transfer(
            sender.user_id, TransferRequest(amount=Decimal("40.00"), recipient_email="bob@example.com")
        )

        assert sender.balance + recipient.balance == total_before

    async def test_raises_on_insufficient_funds(self):
        sender = make_wallet(balance="10.00")
        recipient = make_wallet(balance="20.00")
        repo = FakeWalletRepository({sender.id: sender, recipient.id: recipient})
        repo.register_email("bob@example.com", recipient)
        service = make_service(repo)

        with pytest.raises(InsufficientFundsError):
            await service.transfer(
                sender.user_id, TransferRequest(amount=Decimal("40.00"), recipient_email="bob@example.com")
            )

    async def test_raises_when_recipient_not_found(self):
        sender = make_wallet(balance="100.00")
        repo = FakeWalletRepository({sender.id: sender})
        service = make_service(repo)

        with pytest.raises(WalletNotFoundError):
            await service.transfer(
                sender.user_id, TransferRequest(amount=Decimal("10.00"), recipient_email="ghost@example.com")
            )