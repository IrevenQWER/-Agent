from __future__ import annotations

import json
import logging
import time
from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any

request_id_var: ContextVar[str] = ContextVar("request_id", default="-")


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": self.formatTime(record, "%Y-%m-%dT%H:%M:%S%z"),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
            "request_id": request_id_var.get(),
        }
        payload.update(getattr(record, "event_fields", {}))
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: int = logging.INFO) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger("futureedu_insight")
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)
    root.propagate = False


def log_event(logger: logging.Logger, message: str, **fields: Any) -> None:
    logger.info(message, extra={"event_fields": fields})


@dataclass
class MetricsRegistry:
    counters: Counter[str] = field(default_factory=Counter)
    latency_sum_ms: Counter[str] = field(default_factory=Counter)

    def increment(self, metric: str, value: int = 1) -> None:
        self.counters[metric] += value

    @contextmanager
    def timer(self, metric: str) -> Iterator[None]:
        started = time.perf_counter()
        try:
            yield
        finally:
            elapsed = (time.perf_counter() - started) * 1000
            self.counters[f"{metric}_count"] += 1
            self.latency_sum_ms[metric] += elapsed

    def snapshot(self) -> dict[str, float | int]:
        values: dict[str, float | int] = dict(self.counters)
        for metric, total in self.latency_sum_ms.items():
            count = self.counters.get(f"{metric}_count", 0)
            values[f"{metric}_avg_ms"] = round(total / count, 2) if count else 0
        return values


metrics_registry = MetricsRegistry()
