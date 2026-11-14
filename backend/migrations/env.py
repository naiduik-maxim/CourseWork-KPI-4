import asyncio

from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import Settings
from app.repositories.models import Base


def run_sync(connection):
    """Налаштувати Alembic і виконати міграції."""

    context.configure(
        connection=connection,
        target_metadata=Base.metadata,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_online():
    """Виконати міграції через підключення до БД."""
    engine = create_async_engine(Settings().database_url)

    async with engine.connect() as connection:
        await connection.run_sync(run_sync)

    await engine.dispose()


if context.is_offline_mode():
    context.configure(
        url=Settings().database_url,
        target_metadata=Base.metadata,
        literal_binds=True,
    )

    with context.begin_transaction():
        context.run_migrations()
else:
    asyncio.run(run_online())