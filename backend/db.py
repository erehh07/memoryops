"""SQLite application records (SQLAlchemy 2.x). Learned memory lives in the MemoryStore, not here."""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from typing import Any, Optional

from sqlalchemy import JSON, Boolean, ForeignKey, Integer, String, Text, create_engine, event
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from sqlalchemy.types import TypeDecorator

from .config import settings


class Money(TypeDecorator):
    """Decimal stored as TEXT so SQLite never rounds through floats."""

    impl = String(32)
    cache_ok = True

    def process_bind_param(self, value, dialect):
        return None if value is None else str(Decimal(value))

    def process_result_value(self, value, dialect):
        return None if value is None else Decimal(value)


def utcnow() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSON, list[Any]: JSON}


class Vendor(Base):
    __tablename__ = "vendors"
    code: Mapped[str] = mapped_column(String(8), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    payment_terms: Mapped[str] = mapped_column(String(20))
    currency: Mapped[str] = mapped_column(String(3))
    tax_label: Mapped[str] = mapped_column(String(10))
    tax_rate: Mapped[Decimal] = mapped_column(Money)
    notes: Mapped[str] = mapped_column(Text, default="")  # planted quirk (for the data story; the agent never reads it)


class Batch(Base):
    __tablename__ = "batches"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(60))
    period: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | processed
    processed_at: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)


class PurchaseOrder(Base):
    __tablename__ = "purchase_orders"
    po_number: Mapped[str] = mapped_column(String(20), primary_key=True)
    vendor_code: Mapped[str] = mapped_column(ForeignKey("vendors.code"))
    order_date: Mapped[str] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3))
    lines: Mapped[list[Any]] = mapped_column(JSON)  # [{sku, description, qty, unit_price}]


class GoodsReceipt(Base):
    __tablename__ = "goods_receipts"
    gr_number: Mapped[str] = mapped_column(String(20), primary_key=True)
    po_number: Mapped[str] = mapped_column(ForeignKey("purchase_orders.po_number"))
    received_date: Mapped[str] = mapped_column(String(10))
    lines: Mapped[list[Any]] = mapped_column(JSON)  # [{sku, qty_received}]


class Invoice(Base):
    __tablename__ = "invoices"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_number: Mapped[str] = mapped_column(String(30))
    vendor_code: Mapped[str] = mapped_column(ForeignKey("vendors.code"))
    po_number: Mapped[Optional[str]] = mapped_column(String(20), nullable=True)
    batch_id: Mapped[int] = mapped_column(ForeignKey("batches.id"))
    invoice_date: Mapped[str] = mapped_column(String(10))
    currency: Mapped[str] = mapped_column(String(3))
    lines: Mapped[list[Any]] = mapped_column(JSON)  # [{sku, description, qty, unit_price}]
    subtotal: Mapped[Decimal] = mapped_column(Money)
    tax_amount: Mapped[Decimal] = mapped_column(Money)
    total: Mapped[Decimal] = mapped_column(Money)
    status: Mapped[str] = mapped_column(String(20), default="new")  # new | matched | exception | resolved
    vendor: Mapped[Vendor] = relationship()


class ExceptionRow(Base):
    __tablename__ = "exceptions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    invoice_id: Mapped[int] = mapped_column(ForeignKey("invoices.id"))
    type: Mapped[str] = mapped_column(String(30))
    variance_pct: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    quantity_delta: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    magnitude: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(20), default="open")  # open | recommended | decided
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    invoice: Mapped[Invoice] = relationship()


class Recommendation(Base):
    __tablename__ = "recommendations"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"))
    action: Mapped[str] = mapped_column(String(20))  # approve | reject | adjust | escalate
    amount: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    confidence: Mapped[float] = mapped_column()
    rationale: Mapped[str] = mapped_column(Text)
    cited_ids: Mapped[list[Any]] = mapped_column(JSON, default=list)
    recalled: Mapped[list[Any]] = mapped_column(JSON, default=list)  # snapshot shown in UI
    suggested_approver: Mapped[Optional[str]] = mapped_column(String(120), nullable=True)
    engine: Mapped[str] = mapped_column(String(60))
    memory_backend: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)


class Decision(Base):
    __tablename__ = "decisions"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    exception_id: Mapped[int] = mapped_column(ForeignKey("exceptions.id"))
    recommendation_id: Mapped[Optional[int]] = mapped_column(ForeignKey("recommendations.id"), nullable=True)
    decision: Mapped[str] = mapped_column(String(20))  # approved | rejected | adjusted | escalated
    amount: Mapped[Optional[Decimal]] = mapped_column(Money, nullable=True)
    approver_name: Mapped[str] = mapped_column(String(120))
    approver_role: Mapped[str] = mapped_column(String(120))
    reason: Mapped[str] = mapped_column(Text)
    decided_on: Mapped[str] = mapped_column(String(10))
    is_override: Mapped[bool] = mapped_column(Boolean, default=False)
    source: Mapped[str] = mapped_column(String(20), default="human")  # human | replay
    memory_record_id: Mapped[Optional[str]] = mapped_column(String(40), nullable=True)
    retained: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[str] = mapped_column(String(40), default=utcnow)
    exception: Mapped[ExceptionRow] = relationship()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    ts: Mapped[str] = mapped_column(String(40), default=utcnow)
    actor: Mapped[str] = mapped_column(String(120))
    action: Mapped[str] = mapped_column(String(60))
    entity: Mapped[str] = mapped_column(String(40))
    entity_id: Mapped[str] = mapped_column(String(40))
    details: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)


def make_engine(path: str | None = None):
    path = path or settings.db_path
    if path != ":memory:":
        Path(path).parent.mkdir(parents=True, exist_ok=True)
    eng = create_engine(f"sqlite:///{path}", connect_args={"check_same_thread": False})

    @event.listens_for(eng, "connect")
    def _fk(dbapi_conn, _):
        dbapi_conn.execute("PRAGMA foreign_keys=ON")

    return eng


engine = make_engine()
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)


def init_db(eng=None) -> None:
    Base.metadata.create_all(eng or engine)


def audit(db: Session, actor: str, event_name: str, entity: str, entity_id: Any, **details) -> None:
    db.add(AuditLog(actor=actor, action=event_name, entity=entity, entity_id=str(entity_id), details=details))
