from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text
from sqlalchemy.dialects.postgresql import UUID as PgUUID
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.sql import func

from app.core.database import Base


class Member(Base):
    __tablename__ = "members"

    id: Mapped[UUID] = mapped_column(PgUUID(as_uuid=True), primary_key=True, default=uuid4)
    business_id: Mapped[UUID] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("businesses.id", ondelete="CASCADE"), index=True
    )
    wa_id: Mapped[str] = mapped_column(String(32), index=True)
    display_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    role: Mapped[str] = mapped_column(String(32), default="staff")  # 'owner', 'manager', 'staff'
    status: Mapped[str] = mapped_column(String(32), default="invited")  # 'invited', 'active', 'suspended', 'removed'
    invited_by: Mapped[UUID | None] = mapped_column(
        PgUUID(as_uuid=True), ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    consent_accepted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    removed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
