import asyncio
from uuid import uuid4
import pytest

from app.core.cost_monitor import CostMonitor


def test_per_user_and_dynamic_global_cost_throttling():
    monitor = CostMonitor(per_user_daily_limit_usd=0.50, base_global_limit_usd=10.0)

    user_a = uuid4()
    user_b = uuid4()

    # User A performs 4 operations of $0.10 each ($0.40 total) -> allowed
    for _ in range(4):
        assert monitor.can_spend(user_a, 0.10, active_user_count=2) is True
        monitor.record_spend(user_a, 0.10)

    # User A tries to spend another $0.15 (total $0.55 > $0.50) -> throttled
    assert monitor.can_spend(user_a, 0.15, active_user_count=2) is False

    # User B is unaffected by User A's throttling -> allowed
    assert monitor.can_spend(user_b, 0.20, active_user_count=2) is True


def test_dynamic_global_budget_scaling():
    monitor = CostMonitor(per_user_daily_limit_usd=0.50, base_global_limit_usd=10.0, global_headroom_multiplier=1.25)

    # With 5 users, ceiling is max(10, 5 * 0.50 * 1.25) = $10.00 base floor
    assert monitor.get_dynamic_global_limit(5) == 10.0

    # With 100 users, ceiling scales to 100 * 0.50 * 1.25 = $62.50 (guarantees no throttling if all 100 users spend within $0.50)
    assert monitor.get_dynamic_global_limit(100) == 62.50

    # Test soft alert at 80%
    uid = uuid4()
    # At 0 spend, alert is false
    assert monitor.is_global_alert_threshold_reached(active_user_count=5, threshold_ratio=0.80) is False
    # If spend reaches $8.50 (above 80% of $10.00 floor = $8.00)
    monitor.record_spend(uid, 8.50)
    assert monitor.is_global_alert_threshold_reached(active_user_count=5, threshold_ratio=0.80) is True


@pytest.mark.asyncio
async def test_concurrent_load_simulation():
    """Simulate 20 concurrent users accessing cost monitor simultaneously."""
    monitor = CostMonitor(per_user_daily_limit_usd=0.50, base_global_limit_usd=100.0)
    user_ids = [uuid4() for _ in range(20)]

    async def simulate_user(uid):
        for _ in range(5):
            allowed = monitor.can_spend(uid, 0.05, active_user_count=20)
            if allowed:
                monitor.record_spend(uid, 0.05)
            await asyncio.sleep(0.001)

    await asyncio.gather(*(simulate_user(u) for u in user_ids))

    # All 20 users spent 5 * $0.05 = $0.25 (below $0.50 limit)
    for u in user_ids:
        assert monitor.can_spend(u, 0.10, active_user_count=20) is True


def test_projected_monthly_cost_per_active_user():
    """
    Projected embedding + LLM cost calculation:
    - 25 messages/day (750/mo) * 150 tokens = 112,500 tokens
    - 2 document notes/mo * 2,000 tokens = 4,000 tokens
    - Total tokens: 116,500 tokens/mo
    - Gemini 1.5 Flash input cost ($0.075 / 1M) = ~$0.0087
    - Gemini text-embedding-004 ($0.08 / 1M) = ~$0.0093
    - Total per active user / month = ~$0.018 USD (< 2 cents)
    """
    monthly_tokens = 116500
    llm_cost_per_token = 0.075 / 1_000_000
    embedding_cost_per_token = 0.08 / 1_000_000

    projected_monthly_usd = (monthly_tokens * llm_cost_per_token) + (monthly_tokens * embedding_cost_per_token)
    assert projected_monthly_usd < 0.05  # Must remain under 5 cents per active user/month
