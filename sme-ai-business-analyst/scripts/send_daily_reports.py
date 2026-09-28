"""
[MANUAL/ADMIN ONE-OFF UTILITY ONLY - NOT REQUIRED FOR SYSTEM OPERATION]
Daily reports are dispatched automatically by ReportScheduler in app/main.py
at the configured daily hour (settings.daily_report_hour_local).

Use this script ONLY for manual operational testing, emergency force-dispatch,
or admin troubleshooting.
"""

import asyncio
from app.services.report_delivery import ReportDeliveryService


async def _run() -> None:
    print("Executing manual one-off dispatch of daily reports...")
    count = await ReportDeliveryService().send_daily_reports()
    print(f"Manual daily reports processed: {count}")


def main() -> None:
    asyncio.run(_run())


if __name__ == "__main__":
    main()
