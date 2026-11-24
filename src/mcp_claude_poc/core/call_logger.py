"""Request/response logging for Claude API calls and MCP server calls.

Each call is written as one JSON line to an external log file:

    {"time": "...", "source": "claude" | "mcp", "operation": "...",
     "target": "...", "duration_ms": 123.4, "status": "ok" | "error",
     "request": {...}, "response": {...}, "error": "..."}

Turned on and off with CALL_LOG_ENABLED in .env; the file path is
CALL_LOG_FILE (default: logs/calls.log). When disabled, log_call() returns
immediately and no file is created.

The log holds full conversation content (prompts, tool inputs and outputs),
so treat the file as sensitive. API keys are never part of the logged data.
"""
import json
import logging
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any, AsyncIterator

from mcp_claude_poc import config

_LOGGER_NAME = "mcp_claude_poc.calls"
_logger: logging.Logger | None = None


def _get_logger() -> logging.Logger | None:
    """Create the file logger on first use; None when logging is disabled."""
    global _logger
    if not config.CALL_LOG_ENABLED:
        return None
    if _logger is None:
        path = Path(config.CALL_LOG_FILE)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Rotate so a long session can't grow the file without bound.
        handler = RotatingFileHandler(
            path,
            maxBytes=config.CALL_LOG_MAX_BYTES,
            backupCount=config.CALL_LOG_BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.addHandler(handler)
        # Keep these records out of the console / root logger.
        logger.propagate = False
        _logger = logger
    return _logger


def _to_jsonable(value: Any) -> Any:
    """json.dumps fallback for SDK objects (pydantic models) and other types."""
    if hasattr(value, "model_dump"):
        return value.model_dump(mode="json", exclude_none=True)
    if isinstance(value, (set, tuple)):
        return list(value)
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    return repr(value)


def log_call(
    source: str,
    operation: str,
    target: str,
    request: Any,
    response: Any = None,
    error: BaseException | None = None,
    duration_ms: float | None = None,
) -> None:
    """Write one request/response record. Never raises: logging must not
    break the call it describes."""
    logger = _get_logger()
    if logger is None:
        return
    record = {
        "time": datetime.now(timezone.utc).isoformat(),
        "source": source,
        "operation": operation,
        "target": target,
        "duration_ms": round(duration_ms, 1) if duration_ms is not None else None,
        "status": "error" if error is not None else "ok",
        "request": request,
    }
    if error is not None:
        record["error"] = f"{type(error).__name__}: {error}"
    else:
        record["response"] = response
    try:
        logger.info(json.dumps(record, default=_to_jsonable, ensure_ascii=False))
    except Exception as exc:  # pragma: no cover - defensive
        logger.info(json.dumps({"time": record["time"], "source": source,
                                "operation": operation, "log_error": repr(exc)}))


class _CallRecord:
    """Holder the caller fills with the response inside `logged_call`."""
    response: Any = None


@asynccontextmanager
async def logged_call(
    source: str, operation: str, target: str, request: Any
) -> AsyncIterator[_CallRecord]:
    """Time a call and log its request and response (or error).

        async with logged_call("mcp", "call_tool", server, req) as rec:
            rec.response = await session.call_tool(...)
    """
    rec = _CallRecord()
    start = time.perf_counter()
    try:
        yield rec
    except BaseException as exc:
        log_call(source, operation, target, request, error=exc,
                 duration_ms=(time.perf_counter() - start) * 1000)
        raise
    log_call(source, operation, target, request, response=rec.response,
             duration_ms=(time.perf_counter() - start) * 1000)
