"""
Unit tests for the agent tools built by `build_tools(repo, user_id)`.

These tests must NOT use a real database or make real LLM/HTTP calls.
Use a fake/in-memory WalletRepository so each tool can be exercised in isolation.

The key property under test is DATA SCOPING: every tool must operate exclusively
on data belonging to the `user_id` the tools were built for (BUSINESS_SPEC A-04).
"""
import uuid
from datetime import datetime
from decimal import Decimal

import pytest

from src.agent.tools import build_tools
from src.wallet.models import Transaction, Wallet, WalletStatus

pytestmark = pytest.mark.asyncio


# ---------------------------------------------------------------------------
# Fakes — implement these as part of your submission
# ---------------------------------------------------------------------------

class FakeWalletRepository:
    """In-memory replacement for WalletRepository, seeded with transactions
    belonging to more than one user so scoping can be verified."""

    def __init__(self):
        self.wallets: dict[uuid.UUID, Wallet] = {}  # keyed by user_id
        self.transactions: list[Transaction] = []

    def add_wallet(self, wallet: Wallet) -> None:
        self.wallets[wallet.user_id] = wallet

    def add_transaction(self, tx: Transaction) -> None:
        self.transactions.append(tx)

    async def get_by_user_id(self, user_id):
        return self.wallets.get(user_id)

    async def list_transactions(
        self, wallet_id, *, page=1, page_size=20, type=None, status=None,
        start_date=None, end_date=None, min_amount=None, max_amount=None,
    ):
        items = [t for t in self.transactions if t.wallet_id == wallet_id]
        if type:
            items = [t for t in items if t.type == type]
        if status:
            items = [t for t in items if t.status == status]
        if start_date:
            items = [t for t in items if t.created_at and t.created_at >= start_date]
        if end_date:
            items = [t for t in items if t.created_at and t.created_at <= end_date]
        items.sort(key=lambda t: t.created_at or datetime.min, reverse=True)
        total = len(items)
        offset = (page - 1) * page_size
        return items[offset : offset + page_size], total

    async def aggregate_transactions(
        self, wallet_id, *, operation, type=None, status=None, start_date=None, end_date=None,
    ):
        items = [t for t in self.transactions if t.wallet_id == wallet_id]
        if type:
            items = [t for t in items if t.type == type]
        if status:
            items = [t for t in items if t.status == status]
        if start_date:
            items = [t for t in items if t.created_at and t.created_at >= start_date]
        if end_date:
            items = [t for t in items if t.created_at and t.created_at <= end_date]

        if not items:
            return None  # mirrors the real repo: SQL aggregates over zero rows return NULL

        op = operation.upper()
        amounts = [t.amount for t in items]
        if op == "SUM":
            return sum(amounts)
        if op == "AVG":
            return sum(amounts) / len(amounts)
        if op == "COUNT":
            return len(items)
        if op == "MAX":
            return max(amounts)
        if op == "MIN":
            return min(amounts)
        raise ValueError(f"Unsupported aggregation operation: {operation!r}")

    async def get_top_transactions(
        self, wallet_id, *, n=5, order="largest", type=None, start_date=None, end_date=None,
    ):
        items = [t for t in self.transactions if t.wallet_id == wallet_id]
        if type:
            items = [t for t in items if t.type == type]
        if start_date:
            items = [t for t in items if t.created_at and t.created_at >= start_date]
        if end_date:
            items = [t for t in items if t.created_at and t.created_at <= end_date]
        items.sort(key=lambda t: t.amount, reverse=(order == "largest"))
        return items[:n]

    async def get_transaction(self, transaction_id, wallet_id):
        for t in self.transactions:
            if t.id == transaction_id and t.wallet_id == wallet_id:
                return t
        return None


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

def make_wallet(user_id=None, balance="100.00", status=WalletStatus.ACTIVE, currency="BRL") -> Wallet:
    return Wallet(id=uuid.uuid4(), user_id=user_id or uuid.uuid4(), balance=Decimal(balance),
                  currency=currency, status=status)


def make_transaction(
    wallet_id, *, type="WITHDRAWAL", amount="10.00", status="COMPLETED",
    created_at=None, description=None,
) -> Transaction:
    return Transaction(
        id=uuid.uuid4(),
        wallet_id=wallet_id,
        type=type,
        amount=Decimal(amount),
        balance_before=Decimal("0.00"),
        balance_after=Decimal("0.00"),
        status=status,
        description=description,
        created_at=created_at or datetime(2026, 1, 1),
    )


async def call_tool(tools, name: str, **kwargs) -> dict:
    tool = next(t for t in tools if t.name == name)
    return await tool.ainvoke(kwargs)


# ---------------------------------------------------------------------------
# get_wallet_summary
# ---------------------------------------------------------------------------

class TestGetWalletSummary:
    async def test_returns_balance_status_and_currency_for_the_user(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id, balance="42.50", currency="BRL")
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        tools = build_tools(repo, user_id)

        result = await call_tool(tools, "get_wallet_summary")

        assert result["balance"] == "42.50"
        assert result["currency"] == "BRL"
        assert result["status"] == WalletStatus.ACTIVE

    async def test_handles_user_without_wallet(self):
        repo = FakeWalletRepository()
        tools = build_tools(repo, uuid.uuid4())

        result = await call_tool(tools, "get_wallet_summary")

        assert "error" in result


# ---------------------------------------------------------------------------
# list_transactions
# ---------------------------------------------------------------------------

class TestListTransactions:
    async def test_only_returns_transactions_for_the_scoped_user(self):
        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        wallet_a = make_wallet(user_id=user_a)
        wallet_b = make_wallet(user_id=user_b)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet_a)
        repo.add_wallet(wallet_b)

        tx_a = make_transaction(wallet_a.id, amount="15.00")
        tx_b = make_transaction(wallet_b.id, amount="999.00")
        repo.add_transaction(tx_a)
        repo.add_transaction(tx_b)

        tools = build_tools(repo, user_a)
        result = await call_tool(tools, "list_transactions")

        returned_ids = {t["id"] for t in result["transactions"]}
        assert str(tx_a.id) in returned_ids
        assert str(tx_b.id) not in returned_ids  # A-04: user B's data must never leak

    async def test_applies_type_and_status_filters(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        repo.add_transaction(make_transaction(wallet.id, type="DEPOSIT", status="COMPLETED"))
        repo.add_transaction(make_transaction(wallet.id, type="WITHDRAWAL", status="COMPLETED"))
        repo.add_transaction(make_transaction(wallet.id, type="WITHDRAWAL", status="FAILED"))

        tools = build_tools(repo, user_id)

        by_type = await call_tool(tools, "list_transactions", type="WITHDRAWAL")
        assert all(t["type"] == "WITHDRAWAL" for t in by_type["transactions"])
        assert by_type["total"] == 2

        by_status = await call_tool(tools, "list_transactions", status="FAILED")
        assert by_status["total"] == 1
        assert by_status["transactions"][0]["status"] == "FAILED"

    async def test_returns_empty_result_when_nothing_matches(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        repo.add_transaction(make_transaction(wallet.id, type="WITHDRAWAL"))

        tools = build_tools(repo, user_id)
        result = await call_tool(tools, "list_transactions", type="DEPOSIT")

        assert result["total"] == 0
        assert result["transactions"] == []


# ---------------------------------------------------------------------------
# aggregate_transactions
# ---------------------------------------------------------------------------

class TestAggregateTransactions:
    async def test_sum_over_withdrawals(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        for amount in ("10.00", "20.00", "30.00"):
            repo.add_transaction(make_transaction(wallet.id, type="WITHDRAWAL", amount=amount))

        tools = build_tools(repo, user_id)
        result = await call_tool(tools, "aggregate_transactions", operation="SUM", type="WITHDRAWAL")

        assert Decimal(result["result"]) == Decimal("60.00")

    async def test_count_and_avg(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        repo.add_transaction(make_transaction(wallet.id, amount="10.00"))
        repo.add_transaction(make_transaction(wallet.id, amount="30.00"))

        tools = build_tools(repo, user_id)

        count = await call_tool(tools, "aggregate_transactions", operation="COUNT")
        assert Decimal(count["result"]) == Decimal("2")

        avg = await call_tool(tools, "aggregate_transactions", operation="AVG")
        assert Decimal(avg["result"]) == Decimal("20.00")

    async def test_aggregation_is_scoped_to_user(self):
        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        wallet_a = make_wallet(user_id=user_a)
        wallet_b = make_wallet(user_id=user_b)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet_a)
        repo.add_wallet(wallet_b)
        repo.add_transaction(make_transaction(wallet_a.id, amount="10.00"))
        repo.add_transaction(make_transaction(wallet_b.id, amount="999.00"))  # must not leak into A's sum

        tools = build_tools(repo, user_a)
        result = await call_tool(tools, "aggregate_transactions", operation="SUM")

        assert Decimal(result["result"]) == Decimal("10.00")


# ---------------------------------------------------------------------------
# get_top_transactions
# ---------------------------------------------------------------------------

class TestGetTopTransactions:
    async def test_returns_n_largest(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        for amount in ("5.00", "50.00", "20.00", "80.00", "10.00"):
            repo.add_transaction(make_transaction(wallet.id, amount=amount))

        tools = build_tools(repo, user_id)
        result = await call_tool(tools, "get_top_transactions", n=2, order="largest")

        amounts = [Decimal(t["amount"]) for t in result["transactions"]]
        assert amounts == [Decimal("80.00"), Decimal("50.00")]

    async def test_returns_n_smallest(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        for amount in ("5.00", "50.00", "20.00", "80.00", "10.00"):
            repo.add_transaction(make_transaction(wallet.id, amount=amount))

        tools = build_tools(repo, user_id)
        result = await call_tool(tools, "get_top_transactions", n=2, order="smallest")

        amounts = [Decimal(t["amount"]) for t in result["transactions"]]
        assert amounts == [Decimal("5.00"), Decimal("10.00")]


# ---------------------------------------------------------------------------
# get_transaction_detail
# ---------------------------------------------------------------------------

class TestGetTransactionDetail:
    async def test_returns_detail_for_owned_transaction(self):
        user_id = uuid.uuid4()
        wallet = make_wallet(user_id=user_id)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet)
        tx = make_transaction(wallet.id, amount="33.00", description="groceries")
        repo.add_transaction(tx)

        tools = build_tools(repo, user_id)
        result = await call_tool(tools, "get_transaction_detail", transaction_id=str(tx.id))

        assert result["id"] == str(tx.id)
        assert Decimal(result["amount"]) == Decimal("33.00")
        assert result["description"] == "groceries"

    async def test_refuses_transaction_belonging_to_another_user(self):
        user_a, user_b = uuid.uuid4(), uuid.uuid4()
        wallet_a = make_wallet(user_id=user_a)
        wallet_b = make_wallet(user_id=user_b)
        repo = FakeWalletRepository()
        repo.add_wallet(wallet_a)
        repo.add_wallet(wallet_b)
        tx_b = make_transaction(wallet_b.id, amount="500.00")
        repo.add_transaction(tx_b)

        tools = build_tools(repo, user_a)
        result = await call_tool(tools, "get_transaction_detail", transaction_id=str(tx_b.id))

        # Must look identical to "doesn't exist" — no leaked amount, no leaked
        # description, nothing that confirms the transaction is real but
        # belongs to someone else (A-04).
        assert "error" in result
        assert "amount" not in result