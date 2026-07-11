import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from src.wallet.models import Transaction, TransactionStatus, TransactionType, Wallet


class WalletRepository:
    def __init__(self, session: AsyncSession):
        self.session = session

    async def get_by_user_id(self, user_id: uuid.UUID) -> Wallet | None:
        result = await self.session.execute(select(Wallet).where(Wallet.user_id == user_id))
        return result.scalar_one_or_none()

    async def get_by_email(self, email: str) -> Wallet | None:
        """Needed for T-01: transfers may identify the recipient by email.

        Local import avoids a circular import between the wallet and auth
        packages (auth/models.py likely doesn't need to know about Wallet).
        """
        from src.auth.models import User

        result = await self.session.execute(
            select(Wallet).join(User, User.id == Wallet.user_id).where(User.email == email)
        )
        return result.scalar_one_or_none()

    async def get_for_update(self, wallet_id: uuid.UUID) -> Wallet | None:
        """Acquires a row-level lock. Caller must be inside an open transaction."""
        result = await self.session.execute(
            select(Wallet).where(Wallet.id == wallet_id).with_for_update()
        )
        return result.scalar_one_or_none()

    async def create_transaction(self, **kwargs) -> Transaction:
        tx = Transaction(**kwargs)
        self.session.add(tx)
        await self.session.flush()
        return tx

    async def get_transaction_by_id(self, transaction_id: uuid.UUID) -> Transaction | None:
        """Unlike `get_transaction`, this doesn't require the wallet_id up front —
        needed when you only have a transaction_id and must look up its wallet
        (e.g. reversing a withdrawal from inside the service layer)."""
        result = await self.session.execute(
            select(Transaction).where(Transaction.id == transaction_id)
        )
        return result.scalar_one_or_none()

    async def get_transaction(self, transaction_id: uuid.UUID, wallet_id: uuid.UUID) -> Transaction | None:
        result = await self.session.execute(
            select(Transaction).where(
                Transaction.id == transaction_id,
                Transaction.wallet_id == wallet_id,
            )
        )
        return result.scalar_one_or_none()

    async def get_by_gateway_reference(self, gateway_reference: str) -> Transaction | None:
        result = await self.session.execute(
            select(Transaction).where(Transaction.gateway_reference == gateway_reference)
        )
        return result.scalar_one_or_none()

    async def get_counterparty_email(self, transaction: Transaction) -> str | None:
        """H-03: for transfers, expose who the other side of the transaction
        was, not just an opaque counterpart_transaction_id. Returns None for
        deposits/withdrawals, which have no counterpart."""
        if transaction.counterpart_transaction_id is None:
            return None

        from src.auth.models import User

        result = await self.session.execute(
            select(User.email)
            .join(Wallet, Wallet.user_id == User.id)
            .join(Transaction, Transaction.wallet_id == Wallet.id)
            .where(Transaction.id == transaction.counterpart_transaction_id)
        )
        return result.scalar_one_or_none()

    async def list_transactions(
        self,
        wallet_id: uuid.UUID,
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
        query = select(Transaction).where(Transaction.wallet_id == wallet_id)
        if type:
            query = query.where(Transaction.type == type)
        if status:
            query = query.where(Transaction.status == status)
        if start_date:
            query = query.where(Transaction.created_at >= start_date)
        if end_date:
            query = query.where(Transaction.created_at <= end_date)
        if min_amount is not None:
            query = query.where(Transaction.amount >= min_amount)
        if max_amount is not None:
            query = query.where(Transaction.amount <= max_amount)

        total = await self.session.scalar(select(func.count()).select_from(query.subquery()))
        rows = await self.session.execute(
            query.order_by(Transaction.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size)
        )
        return list(rows.scalars().all()), total or 0

    async def aggregate_transactions(
        self,
        wallet_id: uuid.UUID,
        *,
        operation: str,
        type: TransactionType | None = None,
        status: TransactionStatus | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> Decimal | int | None:
        """A-06: SUM/AVG/COUNT/MAX/MIN over a user's transactions, used by
        the agent for questions like "how much did I spend this month".
        Returns None if no rows match the filters (SQL aggregates over an
        empty set return NULL, not 0 — callers should treat None as
        "nothing found", not as zero)."""
        agg_columns = {
            "SUM": func.sum(Transaction.amount),
            "AVG": func.avg(Transaction.amount),
            "COUNT": func.count(Transaction.id),
            "MAX": func.max(Transaction.amount),
            "MIN": func.min(Transaction.amount),
        }
        op = operation.upper()
        if op not in agg_columns:
            raise ValueError(f"Unsupported aggregation operation: {operation!r}")

        stmt = select(agg_columns[op]).where(Transaction.wallet_id == wallet_id)
        if type:
            stmt = stmt.where(Transaction.type == type)
        if status:
            stmt = stmt.where(Transaction.status == status)
        if start_date:
            stmt = stmt.where(Transaction.created_at >= start_date)
        if end_date:
            stmt = stmt.where(Transaction.created_at <= end_date)

        return await self.session.scalar(stmt)

    async def get_top_transactions(
        self,
        wallet_id: uuid.UUID,
        *,
        n: int = 5,
        order: str = "largest",
        type: TransactionType | None = None,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
    ) -> list[Transaction]:
        """A-06: top-N largest/smallest transactions, e.g. "what was my
        biggest expense"."""
        stmt = select(Transaction).where(Transaction.wallet_id == wallet_id)
        if type:
            stmt = stmt.where(Transaction.type == type)
        if start_date:
            stmt = stmt.where(Transaction.created_at >= start_date)
        if end_date:
            stmt = stmt.where(Transaction.created_at <= end_date)

        if order == "largest":
            stmt = stmt.order_by(Transaction.amount.desc())
        elif order == "smallest":
            stmt = stmt.order_by(Transaction.amount.asc())
        else:
            raise ValueError(f"Unsupported order: {order!r} (expected 'largest' or 'smallest')")

        stmt = stmt.limit(n)
        result = await self.session.execute(stmt)
        return list(result.scalars().all())