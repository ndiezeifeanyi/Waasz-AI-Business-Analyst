"""
[MANUAL/ADMIN ONE-OFF UTILITY ONLY - NOT REQUIRED FOR SYSTEM OPERATION]
Weekly reports are dispatched automatically by ReportScheduler in app/main.py
on Mondays at the configured daily hour (settings.weekly_report_weekday).

Use this script ONLY for manual operational testing, emergency force-dispatch,
or admin troubleshooting.
"""

import asyncio
from app.services.report_delivery import ReportDeliveryService


async def _run() -> None:
    print("Executing manual one-off dispatch of weekly reports...")
    count = await ReportDeliveryService().send_weekly_reports()
    print(f"Manual weekly reports processed: {count}")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
