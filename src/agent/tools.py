import uuid
from datetime import datetime
from typing import Optional

from langchain_core.tools import BaseTool, tool

from src.wallet.repository import WalletRepository


def _parse_date(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        # Raised inside a tool, this gets caught by agent.py's chat loop and
        # turned into {"error": ...}, which the LLM sees and can react to —
        # e.g. asking the user to clarify the date format — rather than the
        # whole request crashing.
        raise ValueError(f"Invalid date format: {value!r}. Use ISO 8601, e.g. '2026-01-01'.")


def _serialize_transaction(tx) -> dict:
    return {
        "id": str(tx.id),
        "type": tx.type,
        "amount": str(tx.amount),
        "status": tx.status,
        "description": tx.description,
        "counterpart_transaction_id": str(tx.counterpart_transaction_id) if tx.counterpart_transaction_id else None,
        "created_at": tx.created_at.isoformat() if tx.created_at else None,
    }


def build_tools(repo: WalletRepository, user_id: uuid.UUID) -> list[BaseTool]:
    """
    Returns a list of LangChain tools scoped to a specific user.
    All tool queries must operate exclusively on data belonging to user_id.

    Every tool below closes over `user_id` from this outer function's
    arguments rather than accepting it as a tool parameter the LLM could
    fill in. This is the actual mechanism behind A-04 ("the agent must not
    expose data from other users") — the LLM never sees or controls which
    user's data it's allowed to query; that's decided once, here, before the
    LLM is ever invoked.
    """

    @tool
    async def get_wallet_summary() -> dict:
        """Returns the current wallet balance, status, and metadata."""
        wallet = await repo.get_by_user_id(user_id)
        if wallet is None:
            return {"error": "No wallet found for this user."}
        return {
            "balance": str(wallet.balance),
            "currency": wallet.currency,
            "status": wallet.status,
            "updated_at": wallet.updated_at.isoformat() if wallet.updated_at else None,
        }

    @tool
    async def list_transactions(
        type: Optional[str] = None,
        status: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
        limit: int = 20,
    ) -> dict:
        """
        Returns a paginated list of transactions.
        type: DEPOSIT | WITHDRAWAL | TRANSFER_DEBIT | TRANSFER_CREDIT
        status: PENDING | COMPLETED | FAILED | REVERSED
        start_date / end_date: ISO 8601 date strings (e.g. '2026-01-01')
        """
        wallet = await repo.get_by_user_id(user_id)
        if wallet is None:
            return {"error": "No wallet found for this user."}

        items, total = await repo.list_transactions(
            wallet.id,
            page=1,
            page_size=limit,
            type=type,
            status=status,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
        )
        return {"total": total, "transactions": [_serialize_transaction(t) for t in items]}

    @tool
    async def aggregate_transactions(
        operation: str,
        type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """
        Computes an aggregation over the user's transactions.
        operation: SUM | AVG | COUNT | MAX | MIN
        type: optional transaction type filter
        """
        wallet = await repo.get_by_user_id(user_id)
        if wallet is None:
            return {"error": "No wallet found for this user."}

        result = await repo.aggregate_transactions(
            wallet.id,
            operation=operation,
            type=type,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
        )
        # None means the aggregate ran over zero matching rows (SQL's NULL,
        # not 0) — surfaced explicitly so the LLM can tell "no transactions
        # matched" apart from "the total was zero".
        return {"operation": operation.upper(), "result": str(result) if result is not None else None}

    @tool
    async def get_top_transactions(
        n: int = 5,
        order: str = "largest",
        type: Optional[str] = None,
        start_date: Optional[str] = None,
        end_date: Optional[str] = None,
    ) -> dict:
        """
        Returns the N largest or smallest transactions.
        order: 'largest' | 'smallest'
        """
        wallet = await repo.get_by_user_id(user_id)
        if wallet is None:
            return {"error": "No wallet found for this user."}

        items = await repo.get_top_transactions(
            wallet.id,
            n=n,
            order=order,
            type=type,
            start_date=_parse_date(start_date),
            end_date=_parse_date(end_date),
        )
        return {"transactions": [_serialize_transaction(t) for t in items]}

    @tool
    async def get_transaction_detail(transaction_id: str) -> dict:
        """Returns full details of a single transaction by its UUID."""
        wallet = await repo.get_by_user_id(user_id)
        if wallet is None:
            return {"error": "No wallet found for this user."}

        try:
            tx_uuid = uuid.UUID(transaction_id)
        except ValueError:
            return {"error": f"{transaction_id!r} is not a valid transaction id."}

        # get_transaction requires BOTH the id and this user's own wallet_id
        # to match — a transaction_id belonging to another user's wallet
        # simply returns None here, indistinguishable from "doesn't exist".
        # That's deliberate: it's the same A-04 scoping enforced everywhere
        # else, applied to a single-record lookup.
        tx = await repo.get_transaction(tx_uuid, wallet.id)
        if tx is None:
            return {"error": "Transaction not found."}
        return _serialize_transaction(tx)

    return [
        get_wallet_summary,
        list_transactions,
        aggregate_transactions,
        get_top_transactions,
        get_transaction_detail,
    ]