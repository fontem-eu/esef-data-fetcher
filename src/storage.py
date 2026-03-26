"""Read/write helpers for the local output directory."""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


def write_registry(registry: dict[str, Any], output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / "eu_entities.json"
    path.write_text(json.dumps(registry, ensure_ascii=False, indent=2), encoding="utf-8")
    logger.info("Wrote registry → %s (%d entries)", path, len(registry))


def read_registry(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "eu_entities.json"
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def write_summary(ticker: str, entity_meta: dict[str, Any], filings: list[dict[str, Any]], output_dir: Path) -> None:
    summaries_dir = output_dir / "summaries"
    summaries_dir.mkdir(parents=True, exist_ok=True)
    doc = {
        **entity_meta,
        "updated_at": datetime.now(timezone.utc).isoformat(),
        "filings": filings,
    }
    safe_name = ticker.replace("/", "_")
    path = summaries_dir / f"{safe_name}.json"
    path.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")


def write_metadata(
    output_dir: Path,
    *,
    total_entities: int,
    fetched: int,
    skipped: int,
    errors: int,
    elapsed_seconds: float,
) -> None:
    path = output_dir / "metadata.json"
    path.write_text(
        json.dumps(
            {
                "generated_at": datetime.now(timezone.utc).isoformat(),
                "total_entities": total_entities,
                "fetched": fetched,
                "skipped": skipped,
                "errors": errors,
                "elapsed_seconds": round(elapsed_seconds, 1),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote metadata → %s", path)
