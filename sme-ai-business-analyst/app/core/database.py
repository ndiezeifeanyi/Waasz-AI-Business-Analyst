"""
Database configuration with security enhancements.
Implements connection pooling, async support, and parameterized queries.
"""

import logging

from sqlalchemy import text
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, QueuePool

AsyncQueuePool = None
try:
    from sqlalchemy.ext.asyncio import AsyncAdaptedQueuePool as AsyncQueuePool
except ImportError:
    try:
        from sqlalchemy.ext.asyncio import FallbackAsyncAdaptedQueuePool as AsyncQueuePool
    except ImportError:
        AsyncQueuePool = None

from app.core.config import settings

logger = logging.getLogger(__name__)


class Base(DeclarativeBase):
    """SQLAlchemy declarative base for all ORM models."""
    pass


def _create_engine() -> AsyncEngine:
    """
    Create async database engine with security configurations.
    
    Security measures:
    - Connection pooling to prevent connection exhaustion DoS
    - Statement timeout to prevent hanging queries
    - SSL required in production
    - Pre-ping to detect stale connections
    """
    
    # In-memory database (tests) uses NullPool
    # Production uses QueuePool with size and overflow limits
    if "sqlite" in settings.database_url:
        pool_class = NullPool
    elif AsyncQueuePool is not None:
        pool_class = AsyncQueuePool
    else:
        pool_class = None  # Let SQLAlchemy pick the default async pool
    
    connect_args = {}
    
    # Add SSL requirement for production
    if settings.app_env == "production" and "postgresql" in settings.database_url:
        connect_args["ssl"] = "require"
    
    # Add statement timeout and connect timeout for asyncpg
    if "postgresql" in settings.database_url:
        connect_args["timeout"] = 25  # asyncpg connection timeout in seconds
        connect_args.setdefault("server_settings", {})
        connect_args["server_settings"]["statement_timeout"] = str(300000)
    
    pool_class_name = pool_class.__name__ if pool_class is not None else "default"
    logger.info(
        f"Creating database engine: env={settings.app_env}, "
        f"pool_class={pool_class_name}, "
        f"pool_size={settings.database_pool_size}"
    )
    
    engine_kwargs = dict(
        echo=settings.app_debug,  # Log SQL in debug mode
        future=True,
        pool_pre_ping=True,  # Verify connections before using
        connect_args=connect_args,
    )
    if pool_class is not None:
        engine_kwargs["poolclass"] = pool_class
        engine_kwargs["pool_size"] = settings.database_pool_size
        engine_kwargs["max_overflow"] = settings.database_max_overflow
    db_url = settings.database_url
    if db_url.startswith("postgresql://"):
        db_url = db_url.replace("postgresql://", "postgresql+asyncpg://", 1)
    elif db_url.startswith("postgres://"):
        db_url = db_url.replace("postgres://", "postgresql+asyncpg://", 1)

    engine = create_async_engine(
        db_url,
        **engine_kwargs
    )
    
    return engine


# Create engine instance
engine: AsyncEngine = _create_engine()

# Session factory
async_session_factory = async_sessionmaker(
    engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db_session():
    """
    Get a database session.
    Use with context manager or dependency injection.
    
    Example:
        async with async_session_factory() as session:
            # Use session
    """
    async with async_session_factory() as session:
        yield session


async def check_db_connection() -> bool:
    """
    Check if database connection is healthy.
    Used for health checks.
    """
    try:
        async with async_session_factory() as session:
            await session.execute(text("SELECT 1"))
        logger.debug("Database connection healthy")
        return True
    except Exception as exc:
        logger.error(f"Database connection failed: {exc}")
        return False

