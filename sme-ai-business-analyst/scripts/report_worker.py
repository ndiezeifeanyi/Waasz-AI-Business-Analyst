"""
[OBSOLETE / SUPERSEDED BY APP STARTUP LIFECYCLE]
This script was previously run as a standalone worker process.
ReportScheduler is now consolidated directly into the FastAPI application's
startup lifecycle (app/main.py) and runs automatically inside the single container.

DO NOT run this script in production; running it alongside the web process will
create duplicate scheduler instances.
"""

import asyncio
from app.core.logging import configure_logging
from app.services.scheduler import ReportScheduler


async def main() -> None:
    print("WARNING: ReportScheduler is already managed by app/main.py.")
    print("Running this standalone script will start a duplicate scheduler instance.")
    configure_logging()
    scheduler = ReportScheduler()
    scheduler.start()
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
