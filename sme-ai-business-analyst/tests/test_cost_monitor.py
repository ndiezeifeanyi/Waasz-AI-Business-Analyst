from decimal import Decimal

import pytest

from app.core.exceptions import CostLimitExceeded
from app.services.cost_monitor import CostMonitor


def test_cost_monitor_blocks_spend_above_daily_limit() -> None:
    monitor = CostMonitor()
    with pytest.raises(CostLimitExceeded):
        monitor.ensure_daily_budget(Decimal("999"), Decimal("1"))
