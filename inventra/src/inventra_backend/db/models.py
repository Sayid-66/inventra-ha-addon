from __future__ import annotations

import enum
from datetime import datetime
from typing import Optional

from uuid import uuid4

from sqlalchemy import CheckConstraint, event, Boolean, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .base import Base


class Unit(Base):
    __tablename__ = "units"
    __table_args__ = (UniqueConstraint("abbreviation", name="uq_unit_abbreviation"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    abbreviation: Mapped[str] = mapped_column(String(32, collation="NOCASE"))
    is_standard: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Product(Base):
    __tablename__ = "products"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    image_url: Mapped[Optional[str]] = mapped_column(String(1024), nullable=True)
    min_stock: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_unit_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    brand: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("units.id", ondelete="RESTRICT"), nullable=True)
    unit: Mapped[Optional[Unit]] = relationship()
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    variant: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    field_provenance: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Barcode(Base):
    __tablename__ = "barcodes"

    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Location(Base):
    __tablename__ = "locations"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_location_normalized_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Store(Base):
    __tablename__ = "stores"
    __table_args__ = (UniqueConstraint("normalized_name", name="uq_store_normalized_name"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    normalized_name: Mapped[str] = mapped_column(String(255))
    version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    deleted_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class Source(str, enum.Enum):
    ANDROID = "ANDROID"
    WEB_UI = "WEB_UI"
    HOME_ASSISTANT = "HOME_ASSISTANT"


class Batch(Base):
    __tablename__ = "batches"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    purchase_event_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("purchase_events.id"), nullable=True
    )
    correction_event_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("correction_events.id"), nullable=True
    )
    location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"))
    mhd: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    event_timestamp: Mapped[int] = mapped_column(Integer)
    is_content_tracked: Mapped[bool] = mapped_column()
    content_unit_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    remaining_quantity: Mapped[int] = mapped_column(Integer)


class PurchaseEvent(Base):
    __tablename__ = "purchase_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    timestamp: Mapped[int] = mapped_column(Integer)
    barcode: Mapped[str] = mapped_column(String(64))
    location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"))
    quantity: Mapped[int] = mapped_column(Integer)
    store_id: Mapped[Optional[str]] = mapped_column(String(36), ForeignKey("stores.id"), nullable=True)
    price_per_unit_cents: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    mhd: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    content_unit_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    content_total: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    content_breakdown: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    user_id: Mapped[str] = mapped_column(String(255))
    source_device_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    source: Mapped[Source] = mapped_column(String(16))


class ConsumptionEvent(Base):
    __tablename__ = "consumption_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    timestamp: Mapped[int] = mapped_column(Integer)
    location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"))
    stock_kind: Mapped[str] = mapped_column(String(16))
    content_unit_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(255))
    source_device_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    source: Mapped[Source] = mapped_column(String(16))


class CorrectionEvent(Base):
    __tablename__ = "correction_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    timestamp: Mapped[int] = mapped_column(Integer)
    location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"))
    stock_kind: Mapped[str] = mapped_column(String(16))
    content_unit_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    old_quantity: Mapped[int] = mapped_column(Integer)
    new_quantity: Mapped[int] = mapped_column(Integer)
    mhd_for_increase: Mapped[Optional[str]] = mapped_column(String(10), nullable=True)
    user_id: Mapped[str] = mapped_column(String(255))
    source_device_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    source: Mapped[Source] = mapped_column(String(16))


class RelocationEvent(Base):
    __tablename__ = "relocation_events"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"))
    timestamp: Mapped[int] = mapped_column(Integer)
    from_location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"))
    to_location_id: Mapped[str] = mapped_column(String(36), ForeignKey("locations.id"))
    stock_kind: Mapped[str] = mapped_column(String(16))
    content_unit_label: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    quantity: Mapped[int] = mapped_column(Integer)
    user_id: Mapped[str] = mapped_column(String(255))
    source_device_id: Mapped[Optional[str]] = mapped_column(String(36), nullable=True)
    source: Mapped[Source] = mapped_column(String(16))


class ChangeKind(str, enum.Enum):
    CREATE = "CREATE"
    UPDATE = "UPDATE"
    DELETE = "DELETE"
    EVENT = "EVENT"


class ChangeLog(Base):
    __tablename__ = "change_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    revision: Mapped[int] = mapped_column(Integer, index=True)
    entity_type: Mapped[str] = mapped_column(String(64))
    entity_id: Mapped[str] = mapped_column(String(64))
    change_kind: Mapped[str] = mapped_column(String(16))
    snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class InstanceMeta(Base):
    __tablename__ = "instance_meta"
    __table_args__ = (CheckConstraint("id = 0", name="ck_instance_meta_single_row"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    instance_id: Mapped[str] = mapped_column(String(36), nullable=False)
    created_at: Mapped[Optional[datetime]] = mapped_column(DateTime, default=datetime.utcnow)


@event.listens_for(InstanceMeta.__table__, "after_create")
def _seed_instance_meta(target, connection, **kwargs) -> None:
    connection.execute(target.insert().values(id=0, instance_id=str(uuid4()), created_at=datetime.utcnow()))


class RevisionCounter(Base):
    __tablename__ = "revision_counter"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    current_revision: Mapped[int] = mapped_column(Integer, default=0)


class ProcessedOperation(Base):
    __tablename__ = "processed_operations"

    operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    payload_hash: Mapped[str] = mapped_column(String(64))
    result_snapshot: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class Device(Base):
    __tablename__ = "devices"
    __table_args__ = (UniqueConstraint("token_hash", name="uq_device_token_hash"),)

    device_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255))
    device_name: Mapped[str] = mapped_column(String(255))
    token_hash: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    revoked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_seen_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    default_location_id: Mapped[Optional[str]] = mapped_column(
        String(36), ForeignKey("locations.id"), nullable=True
    )


class PairingCode(Base):
    __tablename__ = "pairing_codes"

    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
    consumed_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)


class MhdWarningAckState(Base):
    __tablename__ = "mhd_warning_ack_state"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    acknowledged_signature: Mapped[str] = mapped_column(String(1024), default="")


class BringWatchStateEnum(str, enum.Enum):
    PENDING_ADD = "PENDING_ADD"
    ON_LIST_CONFIRMED = "ON_LIST_CONFIRMED"
    LOCKED_PURCHASED = "LOCKED_PURCHASED"
    ERROR = "ERROR"


class BringWatchOrigin(str, enum.Enum):
    INVENTRA_CREATED = "INVENTRA_CREATED"
    ADOPTED_EXISTING = "ADOPTED_EXISTING"


class BringWatchState(Base):
    __tablename__ = "bring_watch_state"

    product_id: Mapped[str] = mapped_column(String(36), ForeignKey("products.id"), primary_key=True)
    state: Mapped[str] = mapped_column(String(20))
    origin: Mapped[str] = mapped_column(String(20))
    bring_item_name: Mapped[str] = mapped_column(String(255))
    bring_uid: Mapped[Optional[str]] = mapped_column(String(64), nullable=True)
    lock_reason: Mapped[Optional[str]] = mapped_column(String(32), nullable=True)
    retry_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    confirmation_deadline_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[Optional[str]] = mapped_column(String(255), nullable=True)
    last_checked_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)
    updated_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)


class ResolverSourceCache(Base):
    __tablename__ = "resolver_source_cache"

    barcode: Mapped[str] = mapped_column(String(64), primary_key=True)
    source: Mapped[str] = mapped_column(String(16), primary_key=True)
    status: Mapped[str] = mapped_column(String(16))
    candidate_json: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)


class ResolutionResult(Base):
    __tablename__ = "resolution_results"

    resolution_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    barcode: Mapped[str] = mapped_column(String(64))
    proposed_fields_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime)
    expires_at: Mapped[datetime] = mapped_column(DateTime)
