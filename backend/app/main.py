from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.routes import router
from app.core.config import Settings
from app.repositories.database import create_database


def create_app(settings: Settings | None = None) -> FastAPI:
    """Створити та налаштувати застосунок FastAPI."""
    if settings is None:
        settings = Settings()

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        """Підготувати ресурси та звільнити їх після зупинки."""
        engine, sessions = create_database(settings)

        application.state.engine = engine
        application.state.sessions = sessions

        try:
            yield
        finally:
            await engine.dispose()

    application = FastAPI(
        title="Контроль якості енергетичної телеметрії",
        description="Мікросервіс контролю якості часових рядів.",
        version="0.1.0",
        lifespan=lifespan,
    )

    application.state.settings = settings

    application.include_router(router)

    return application


app = create_app()