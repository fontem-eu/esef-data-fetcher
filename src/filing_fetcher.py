"""
Extracts financial summaries from pre-fetched filing URLs.

No API calls here — the filing metadata (json_url, end_date) comes from the
entity registry build step which already fetched everything in one bulk request.
"""
from __future__ import annotations

import logging
from datetime import date
from typing import Any

import requests

from .ifrs_mapper import extract_summary

logger = logging.getLogger(__name__)


def _parse_date(s: str | None) -> date | None:
    if not s:
        return None
    try:
        return date.fromisoformat(str(s)[:10])
    except ValueError:
        return None


def _fiscal_year(end: date) -> int:
    return end.year


def fetch_entity_summaries(
    lei: str,
    filing_refs: list[dict[str, Any]],
    *,
    request_timeout: int = 30,
) -> list[dict[str, Any]]:
    """
    Download and extract financial summaries for one entity.

    Args:
        lei: Legal Entity Identifier (for logging only).
        filing_refs: List of {end_date, json_url, filing_index} dicts,
                     sorted newest-first — comes from entity_registry.build_registry().
        request_timeout: HTTP request timeout in seconds.

    Returns:
        List of yearly summary dicts, sorted by year descending.
        Each dict: {year, filing_date, filing_index, <21 financial fields>}
    """
    summaries: list[dict[str, Any]] = []
    seen_years: set[int] = set()

    for ref in filing_refs:
        end_date = _parse_date(ref.get("end_date"))
        if end_date is None:
            continue

        year = _fiscal_year(end_date)
        if year in seen_years:
            continue  # skip amendments / duplicates
        seen_years.add(year)

        json_url = ref.get("json_url")
        if not json_url:
            continue

        facts = _download_facts(json_url, timeout=request_timeout)
        if facts is None:
            continue

        summary = extract_summary(facts, end_date)
        if all(v is None for v in summary.values()):
            logger.debug("No usable facts for %s FY%d (%s)", lei, year, json_url)
            continue

        summaries.append({
            "year": year,
            "filing_date": ref.get("end_date", "")[:10] or None,
            "filing_index": ref.get("filing_index", ""),
            **summary,
        })

    return summaries


def _download_facts(url: str, timeout: int) -> dict[str, Any] | None:
    """Download the xBRL-JSON report and return the 'facts' dict."""
    try:
        resp = requests.get(url, timeout=timeout)
        resp.raise_for_status()
        return resp.json().get("facts", {})
    except Exception as exc:  # pylint: disable=broad-except
        logger.warning("Failed to download %s: %s", url, exc)
        return None
