import secrets
from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.magic_link import MagicLink


class MagicLinkService:
    async def create_link(
        self, db: AsyncSession, business_id: UUID, expires_in_days: int = 2
    ) -> str:
        token = secrets.token_urlsafe(32)
        expires_at = datetime.now(UTC) + timedelta(days=expires_in_days)
        
        link = MagicLink(
            business_id=business_id,
            token=token,
            expires_at=expires_at,
        )
        db.add(link)
        await db.flush()
        return token

    async def validate_token(self, db: AsyncSession, token: str) -> UUID | None:
        result = await db.execute(
            select(MagicLink).where(
                MagicLink.token == token,
                MagicLink.expires_at > datetime.now(UTC),
                MagicLink.is_used == False,
            )
        )
        link = result.scalar_one_or_none()
        if not link:
            return None
        return link.business_id
