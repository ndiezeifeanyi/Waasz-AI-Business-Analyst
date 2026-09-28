from datetime import UTC, datetime
from decimal import Decimal
import logging
from uuid import UUID

from app.core.config import settings

logger = logging.getLogger(__name__)


class CostMonitor:
    """
    Dynamic Cost Monitor:
    - Per-user daily cap ($0.50/day) to prevent single-tenant exhaustion.
    - Dynamic global ceiling: max(10.0, active_users_count * 0.35).
    - Tracks AI inference, embedding tokens, and report generation spend.
    """

    def __init__(
        self,
        per_user_daily_limit_usd: float = 0.50,
        base_global_limit_usd: float = 10.0,
        global_headroom_multiplier: float = 1.25,
    ) -> None:
        self.per_user_daily_limit_usd = per_user_daily_limit_usd
        self.base_global_limit_usd = base_global_limit_usd
        self.global_headroom_multiplier = global_headroom_multiplier
        self._user_daily_spend: dict[str, float] = {}
        self._last_reset_day: str = datetime.now(UTC).strftime("%Y-%m-%d")

    def _maybe_reset(self) -> None:
        today = datetime.now(UTC).strftime("%Y-%m-%d")
        if today != self._last_reset_day:
            self._user_daily_spend.clear()
            self._last_reset_day = today

    def get_dynamic_global_limit(self, active_user_count: int) -> float:
        """
        Global ceiling scales with active users with a headroom multiplier (default 1.25x).
        Ensures that legitimate users spending within their $0.50/day cap are NEVER throttled
        by the global cap under normal operations.
        Example: 100 active users * $0.50 * 1.25 = $62.50 ceiling (vs $50 max legitimate spend).
        """
        scaled_ceiling = active_user_count * self.per_user_daily_limit_usd * self.global_headroom_multiplier
        return max(self.base_global_limit_usd, scaled_ceiling)

    def is_global_alert_threshold_reached(self, active_user_count: int = 1, threshold_ratio: float = 0.80) -> bool:
        """
        Soft alert threshold (80% of global ceiling). Triggers logging/monitoring without blocking requests.
        """
        self._maybe_reset()
        total_spend = sum(self._user_daily_spend.values())
        return total_spend >= (self.get_dynamic_global_limit(active_user_count) * threshold_ratio)

    def can_spend(self, user_id: str | UUID, estimated_cost_usd: float, active_user_count: int = 1) -> bool:
        """
        Enforce dual caps:
        1. Hard per-user cap ($0.50/day): Throttles any single abusive/runaway user.
        2. Systemic global circuit-breaker: Throttles only if system-wide spend exceeds
           max(base, active_users * $0.50 * 1.25), protecting against systemic runaway spend.
        """
        self._maybe_reset()
        uid = str(user_id)
        current_user_spend = self._user_daily_spend.get(uid, 0.0)

        # 1. Enforce Per-User Daily Limit ($0.50)
        if current_user_spend + estimated_cost_usd > self.per_user_daily_limit_usd:
            logger.warning("User %s exceeded daily spend limit of $%0.2f", uid, self.per_user_daily_limit_usd)
            return False

        # 2. Check Global Alert Threshold (80% soft alert)
        if self.is_global_alert_threshold_reached(active_user_count, 0.80):
            logger.warning(
                "Global daily AI spend reached 80% of dynamic limit ($%0.2f)",
                self.get_dynamic_global_limit(active_user_count)
            )

        # 3. Enforce Dynamic Global Circuit Breaker (100% hard ceiling)
        global_limit = self.get_dynamic_global_limit(active_user_count)
        total_spend = sum(self._user_daily_spend.values())
        if total_spend + estimated_cost_usd > global_limit:
            logger.error("Dynamic global spend circuit-breaker tripped ($%0.2f limit)", global_limit)
            return False

        return True

    def record_spend(self, user_id: str | UUID, cost_usd: float) -> None:
        self._maybe_reset()
        uid = str(user_id)
        self._user_daily_spend[uid] = self._user_daily_spend.get(uid, 0.0) + cost_usd


# Global instance
cost_monitor = CostMonitor()
