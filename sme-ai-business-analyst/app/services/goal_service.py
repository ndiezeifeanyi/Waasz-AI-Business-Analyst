from datetime import datetime
from decimal import Decimal
import logging
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.goal import UserGoal

logger = logging.getLogger(__name__)


class GoalService:
    async def create_goal(
        self,
        db: AsyncSession,
        user_id: UUID,
        title: str,
        category: str = "productivity",
        target_value: Decimal | None = None,
        target_date: datetime | None = None,
        business_id: UUID | None = None,
        visibility: str = "private",
        created_by_member_id: UUID | None = None,
    ) -> UserGoal:
        goal = UserGoal(
            user_id=user_id,
            business_id=business_id,
            created_by_member_id=created_by_member_id,
            title=title,
            category=category,
            target_value=target_value,
            current_value=Decimal("0.00"),
            target_date=target_date,
            visibility=visibility,
            status="in_progress",
        )
        db.add(goal)
        await db.flush()
        logger.info("Created goal %s for user %s: '%s'", goal.id, user_id, title)
        return goal

    async def get_active_goals(self, db: AsyncSession, user_id: UUID) -> list[UserGoal]:
        stmt = (
            select(UserGoal)
            .where(UserGoal.user_id == user_id, UserGoal.status == "in_progress")
            .order_by(UserGoal.created_at.desc())
        )
        result = await db.execute(stmt)
        return list(result.scalars().all())

    async def record_goal_progress(
        self, db: AsyncSession, goal_id: UUID, increment: Decimal
    ) -> UserGoal | None:
        goal = await db.get(UserGoal, goal_id)
        if goal:
            goal.current_value = (goal.current_value or Decimal("0.00")) + increment
            if goal.target_value and goal.current_value >= goal.target_value:
                goal.status = "achieved"
            await db.flush()
        return goal
