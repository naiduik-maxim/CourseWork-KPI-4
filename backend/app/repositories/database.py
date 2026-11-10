from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)

from app.core.config import Settings


def create_database(settings: Settings) -> tuple[AsyncEngine, async_sessionmaker[AsyncSession]]:
    """Створити engine та фабрику асинхронних сесій."""

    engine =  create_async_engine(
        settings.database_url,
        pool_size = settings.db_pool_size,
        max_overflow=settings.db_max_overflow,
        pool_pre_ping=True,
        pool_timeout=5,
        isolation_level="READ COMMITTED",
        connect_args={
            "timeout": 5,
            "command_timeout": 10,
        },
    )

    sessions = async_sessionmaker(
        bind=engine,
        expire_on_commit=False,
    )

    return engine, sessions