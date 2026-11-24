import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from contextlib import asynccontextmanager
from time import perf_counter
from uuid import uuid4
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from sqlalchemy.exc import SQLAlchemyError
from starlette.responses import JSONResponse

from app.api.middleware import BodyLimitMiddleware
from app.api.routes import router
from app.core.config import Settings
from app.core.logging import configure_logging
from app.repositories.database import create_database
from app.services.telemetry import (
    AnomalyDetectionService,
    ServiceError,
    TelemetryService,
)

def create_app(
    settings: Settings | None = None,
    *,
    detector=None,
    metadata: dict | None = None,  
):
    settings = settings or Settings()


    @asynccontextmanager
    async def lifespan(application):
        logger = configure_logging(settings.log_dir, settings.instance_id)
        application.state.logger = logger

        engine, sessions = create_database(settings)
        application.state.sessions = sessions
        application.state.engine = engine
        application.state.service = None
        application.state.model_metadata = {}

        executor = ThreadPoolExecutor(
            max_workers=settings.inference_workers,
            thread_name_prefix="inference",
        )

        try:
            registry = {}
            limiter = asyncio.Semaphore(settings.inference_workers)

            if detector is not None:
                loaded_metadata = metadata or {}
                registry[loaded_metadata.get("model_version")] = (
                    AnomalyDetectionService(detector, executor, limiter),
                    loaded_metadata,
                )
            else:
                from app.ml.loader import load_detector

                for model_path in (settings.model_dir, settings.electrical_model_dir):
                    try:
                        loaded = load_detector(model_path)
                        loaded_metadata = json.loads(
                            (model_path / "metadata.json").read_text(encoding="utf-8")
                        )
                        registry[loaded_metadata["model_version"]] = (
                            AnomalyDetectionService(loaded, executor, limiter),
                            loaded_metadata,
                        )
                    except Exception as error:
                        logger.error(
                            "model_unavailable",
                            extra={"error_type": type(error).__name__},
                        )

            application.state.model_registry = {
                version: entry[1] for version, entry in registry.items()
            }

            if registry:
                first_anomaly, first_metadata = next(iter(registry.values()))
                application.state.model_metadata = first_metadata
                application.state.service = TelemetryService(
                    sessions,
                    first_anomaly,
                    first_metadata,
                    registry,
                )

            yield

        finally:
            executor.shutdown(wait=True, cancel_futures=True)
            await engine.dispose()

    application = FastAPI(
        title="Контроль якості енергетичної телеметрії",
        version="0.1.0",
        lifespan=lifespan,
    )
    application.add_middleware(
        BodyLimitMiddleware,
        max_bytes=settings.max_body_bytes,
    )

    @application.middleware("http")
    async def request_log(request: Request, call_next):
        request_id = str(uuid4())
        request.state.request_id = request_id
        start = perf_counter()
        status = 500

        try:
            response = await call_next(request)
            status = response.status_code
            response.headers["X-Request-ID"] = request_id
            return response
        finally:
            request.app.state.logger.info(
                "http_request",
                extra={
                    "request_id": request_id,
                    "duration_ms": round((perf_counter() - start) * 1000, 3),
                    "status": status,
                },
            )

    @application.exception_handler(RequestValidationError)
    async def validation_error(request, error):
        # Повертаємо місце та тип помилки без початкових значень запиту.
        details = [
            {"loc": e["loc"], "msg": e["msg"], "type": e["type"]}
            for e in error.errors()
        ]
        return JSONResponse({"detail": details}, status_code=422)

    @application.exception_handler(ServiceError)
    async def service_error(request, error):
        return JSONResponse(
            {"detail": error.detail},
            status_code=error.status,
        )

    async def database_error(request, error):
        request.app.state.logger.error(
            "database_unavailable",
            extra={
                "request_id": request.state.request_id,
                "error_type": type(error).__name__,
            },
        )
        return JSONResponse(
            {"detail": "Database operation could not be confirmed; retry safely"},
            status_code=503,
        )

    for exception in (SQLAlchemyError, OSError, TimeoutError):
        application.add_exception_handler(exception, database_error)

    @application.exception_handler(Exception)
    async def unexpected_error(request, error):
        request.app.state.logger.error(
            "unhandled_error",
            extra={
                "request_id": getattr(request.state, "request_id", None),
                "error_type": type(error).__name__,
            },
        )
        return JSONResponse(
            {"detail": "Internal server error"},
            status_code=500,
        )

    application.include_router(router)
    return application


app = create_app()