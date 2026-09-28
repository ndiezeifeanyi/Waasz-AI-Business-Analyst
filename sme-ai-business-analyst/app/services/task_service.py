from datetime import UTC, datetime, timedelta
from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.activity import Activity
from app.models.task import Task


class TaskService:
    async def create_task(
        self,
        db: AsyncSession,
        business_id: UUID,
        title: str,
        description: str | None = None,
        due_at: datetime | None = None,
        user_id: UUID | None = None,
        is_alarm_mode: bool = False,
        repeat_interval_seconds: int = 60,
        max_repeats: int = 10,
    ) -> Task:
        task = Task(
            business_id=business_id,
            user_id=user_id,
            title=title,
            description=description,
            due_at=due_at,
            is_alarm_mode=is_alarm_mode,
            repeat_interval_seconds=repeat_interval_seconds,
            max_repeats=max_repeats,
        )
        db.add(task)
        await db.flush()
        return task

    async def complete_task(self, db: AsyncSession, business_id: UUID, task_id: UUID) -> bool:
        result = await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.business_id == business_id)
            .values(completed_at=datetime.utcnow())
        )
        return result.rowcount > 0

    async def get_pending_tasks(self, db: AsyncSession, business_id: UUID) -> list[Task]:
        result = await db.execute(
            select(Task)
            .where(Task.business_id == business_id, Task.completed_at == None)
            .order_by(Task.due_at.asc().nullslast(), Task.created_at.desc())
        )
        return list(result.scalars().all())

    async def log_activity(
        self, db: AsyncSession, business_id: UUID, category: str, description: str
    ) -> Activity:
        activity = Activity(
            business_id=business_id,
            category=category,
            description=description,
        )
        db.add(activity)
        await db.flush()
        return activity

    async def get_recent_activities(
        self, db: AsyncSession, business_id: UUID, limit: int = 10
    ) -> list[Activity]:
        result = await db.execute(
            select(Activity)
            .where(Activity.business_id == business_id)
            .order_by(Activity.occurred_at.desc())
            .limit(limit)
        )
        return list(result.scalars().all())

    async def get_latest_pending_task(
        self, db: AsyncSession, business_id: UUID
    ) -> Task | None:
        """Find the most recently created pending task for a business."""
        result = await db.execute(
            select(Task)
            .where(
                Task.business_id == business_id,
                Task.completed_at.is_(None),
            )
            .order_by(Task.created_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def get_latest_active_alarm(
        self, db: AsyncSession, business_id: UUID, user_id: UUID | None = None
    ) -> Task | None:
        """Find the most recent alarm task for a business/user."""
        query = select(Task).where(
            Task.business_id == business_id,
            Task.is_alarm_mode.is_(True),
            Task.completed_at.is_(None),
        )
        if user_id:
            query = query.where(Task.user_id == user_id)
        query = query.order_by(Task.created_at.desc()).limit(1)
        result = await db.execute(query)
        return result.scalar_one_or_none()

    async def update_task_due_at(
        self, db: AsyncSession, task_id: UUID, due_at: datetime
    ) -> Task | None:
        """Update a task's due_at timestamp."""
        task = await db.get(Task, task_id)
        if task:
            task.due_at = due_at
            await db.flush()
        return task

    async def get_reminders_to_send(self, db: AsyncSession) -> list[Task]:
        """Find tasks that are due and haven't had their first reminder sent yet."""
        now = datetime.now(UTC)
        result = await db.execute(
            select(Task)
            .where(
                Task.completed_at.is_(None),
                Task.due_at.is_not(None),
                Task.due_at <= now,
                Task.reminder_sent_at.is_(None),
            )
            .order_by(Task.due_at.asc())
        )
        return list(result.scalars().all())

    async def mark_reminder_sent(
        self, db: AsyncSession, task_id: UUID, is_alarm_mode: bool = False
    ) -> bool:
        """Atomically mark a reminder as sent to prevent double-sends on first dispatch."""
        now = datetime.now(UTC)
        values = {"reminder_sent_at": now}
        if is_alarm_mode:
            values["last_repeat_sent_at"] = now
            values["repeat_count"] = 0

        result = await db.execute(
            update(Task)
            .where(
                Task.id == task_id,
                Task.completed_at.is_(None),
                Task.reminder_sent_at.is_(None),
            )
            .values(**values)
        )
        await db.commit()
        return result.rowcount > 0

    async def get_alarm_repeats_to_send(self, db: AsyncSession) -> list[Task]:
        """Find active alarm-mode tasks that have elapsed their repeat interval."""
        now = datetime.now(UTC)
        result = await db.execute(
            select(Task)
            .where(
                Task.is_alarm_mode.is_(True),
                Task.completed_at.is_(None),
                Task.dismissed_at.is_(None),
                Task.reminder_sent_at.is_not(None),
                Task.last_repeat_sent_at.is_not(None),
                Task.repeat_count < Task.max_repeats,
            )
            .order_by(Task.last_repeat_sent_at.asc())
        )
        tasks = list(result.scalars().all())
        due_repeats = []
        for task in tasks:
            if not task.last_repeat_sent_at:
                continue
            last_sent = (
                task.last_repeat_sent_at.replace(tzinfo=UTC)
                if task.last_repeat_sent_at.tzinfo is None
                else task.last_repeat_sent_at
            )
            interval = timedelta(seconds=task.repeat_interval_seconds)
            if (now - last_sent) >= interval:
                due_repeats.append(task)
        return due_repeats

    async def claim_alarm_repeat(
        self,
        db: AsyncSession,
        task_id: UUID,
        current_repeat_count: int,
        expected_last_sent: datetime,
        is_final: bool = False,
    ) -> bool:
        """
        Atomically claim an alarm repeat dispatch to prevent concurrent sends.
        Increments repeat_count and updates last_repeat_sent_at.
        If is_final is True, also sets dismissed_at to gracefully stop repeats.
        """
        now = datetime.now(UTC)
        values = {
            "last_repeat_sent_at": now,
            "repeat_count": Task.repeat_count + 1,
        }
        if is_final:
            values["dismissed_at"] = now

        stmt = (
            update(Task)
            .where(
                Task.id == task_id,
                Task.is_alarm_mode.is_(True),
                Task.completed_at.is_(None),
                Task.dismissed_at.is_(None),
                Task.repeat_count == current_repeat_count,
                Task.last_repeat_sent_at == expected_last_sent,
            )
            .values(**values)
        )
        result = await db.execute(stmt)
        await db.commit()
        return result.rowcount > 0

    async def dismiss_alarm(self, db: AsyncSession, task_id: UUID) -> bool:
        """Atomically dismiss an alarm task so no further repeats fire."""
        now = datetime.now(UTC)
        result = await db.execute(
            update(Task)
            .where(Task.id == task_id, Task.dismissed_at.is_(None))
            .values(dismissed_at=now)
        )
        await db.commit()
        return result.rowcount > 0

    async def snooze_alarm(
        self, db: AsyncSession, task_id: UUID, snooze_minutes: int = 10
    ) -> Task | None:
        """
        Snooze an alarm:
        Set due_at = now() + 10 minutes,
        reset repeat_count = 0, last_repeat_sent_at = NULL, reminder_sent_at = NULL,
        keep dismissed_at = NULL.
        """
        now = datetime.now(UTC)
        task = await db.get(Task, task_id)
        if task:
            task.due_at = now + timedelta(minutes=snooze_minutes)
            task.reminder_sent_at = None
            task.last_repeat_sent_at = None
            task.repeat_count = 0
            task.dismissed_at = None
            await db.commit()
            await db.refresh(task)
        return task
