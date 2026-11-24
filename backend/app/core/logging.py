import json
import logging
import os
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        data = {
            "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
            "level": record.levelname,
            "event": record.getMessage(),
        }
        for key in ("request_id", "sensor_id", "duration_ms", "status", "error_type"):
            if hasattr(record, key):
                data[key] = getattr(record, key)
        # Do not include exception strings: DB exceptions can embed input SQL values.
        if record.exc_info:
            data["error_type"] = record.exc_info[0].__name__
        return json.dumps(data, ensure_ascii=False)


def configure_logging(directory: Path, instance_id: str) -> logging.Logger:
    directory.mkdir(parents=True, exist_ok=True)
    logger = logging.getLogger("telemetry")
    logger.setLevel(logging.INFO)
    for handler in logger.handlers[:]:
        logger.removeHandler(handler)
        handler.close()
    safe_id = "".join(c for c in instance_id if c.isalnum() or c in "-_") or "api"
    formatter = JsonFormatter()
    file_handler = RotatingFileHandler(directory / f"{safe_id}-{os.getpid()}.jsonl", maxBytes=5_000_000, backupCount=3, encoding="utf-8")
    stream_handler = logging.StreamHandler()
    for handler in (file_handler, stream_handler):
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    logger.propagate = False
    return logger
