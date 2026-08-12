from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def load_prompt(path: Path | str) -> dict[str, Any]:
    payload = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    required = {"name", "version", "system", "instruction"}
    missing = required - set(payload or {})
    if missing:
        raise ValueError(f"Prompt 文件缺少字段: {', '.join(sorted(missing))}")
    return payload
