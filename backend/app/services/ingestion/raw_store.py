"""Persist raw provider responses under data/raw/<provider>/<date>/ — only where permitted."""

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)


class RawStore:
    def __init__(self, base_dir: Path, allowed: dict[str, bool]) -> None:
        self.base_dir = base_dir
        self.allowed = allowed

    def save(self, provider: str, run_id: str, pages: list[dict], *, data_origin: str) -> list[Path]:
        # Mock payloads already live in data/sample; never copy them into data/raw.
        if data_origin != "live" or not self.allowed.get(provider, False) or not pages:
            return []
        folder = self.base_dir / provider / datetime.now(UTC).strftime("%Y%m%d")
        folder.mkdir(parents=True, exist_ok=True)
        paths = []
        for i, page in enumerate(pages):
            path = folder / f"{run_id}_{i:03d}.json"
            path.write_text(json.dumps(page, ensure_ascii=False), encoding="utf-8")
            paths.append(path)
        logger.info("raw_saved", extra={"provider": provider, "files": len(paths)})
        return paths
