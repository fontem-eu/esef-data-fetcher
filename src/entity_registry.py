"""
Builds the EU entity registry by:
  1. Fetching all filings from filings.xbrl.org in one bulk call (via xbrl_filings_api)
  2. Grouping filings by entity LEI, keeping the N most recent per entity
  3. Resolving each LEI to an exchange-suffixed ticker (OpenFIGI + fallback)
  4. Returning (registry, filing_urls) so callers can extract financials without
     any additional API calls.
"""
from __future__ import annotations

import logging
from collections import defaultdict
from typing import Any

import xbrl_filings_api as xf

from .config import Config
from .exchange_map import resolve_tickers

logger = logging.getLogger(__name__)


def build_registry(cfg: Config) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:  # pylint: disable=too-many-locals
    """
    Fetch all ESEF/UKSEF filings from filings.xbrl.org in one bulk request,
    resolve tickers, and return:

      registry:     {ticker: entity_metadata}   → for eu_entities.json
      filing_urls:  {lei: [{year, end_date, json_url, filing_index}, …]}
                    → already-fetched filing metadata; no further API calls needed
    """
    logger.info("Fetching all filings from filings.xbrl.org …")
    all_filings = xf.get_filings(
        flags=xf.GET_ENTITY,
        limit=0,  # no limit — fetch everything
    )
    logger.info("Retrieved %d filings total", len(all_filings))

    # ── Group filings by LEI ─────────────────────────────────────────────────
    # entity_meta[lei]  = {lei, name, country}
    # lei_filings[lei]  = sorted list of {end_date_str, json_url, filing_index}
    entity_meta: dict[str, dict[str, Any]] = {}
    lei_filings: dict[str, list[dict[str, Any]]] = defaultdict(list)

    for filing in all_filings:
        entity = filing.entity
        if entity is None:
            continue
        lei = entity.identifier
        if not lei:
            continue

        json_url = getattr(filing, "json_url", None)
        end_str = str(getattr(filing, "last_end_date", "") or "")
        country = (getattr(filing, "country", "") or "").upper()[:2]

        if lei not in entity_meta:
            entity_meta[lei] = {
                "lei": lei,
                "name": entity.name or "",
                "country": country,
            }

        if json_url and end_str:
            lei_filings[lei].append({
                "end_date": end_str,
                "json_url": json_url,
                "filing_index": filing.filing_index,
            })

    # Sort each entity's filings by date descending, keep N most recent
    filing_urls: dict[str, list[dict[str, Any]]] = {}
    for lei, filings in lei_filings.items():
        filings.sort(key=lambda f: f["end_date"], reverse=True)
        filing_urls[lei] = filings[: cfg.max_filings_per_entity]

    entities = list(entity_meta.values())
    logger.info("Unique entities with filings: %d", len(entities))

    # ── Resolve tickers ──────────────────────────────────────────────────────
    logger.info("Resolving tickers (OpenFIGI=%s) …", cfg.use_openfigi)
    ticker_map = resolve_tickers(
        entities,
        use_openfigi=cfg.use_openfigi,
        api_key=cfg.openfigi_api_key,
        batch_size=cfg.openfigi_batch_size,
        timeout=cfg.request_timeout,
    )

    # ── Build registry ───────────────────────────────────────────────────────
    # Keyed by ticker; disambiguate duplicates by appending last 4 chars of LEI
    ticker_count: dict[str, int] = defaultdict(int)
    for tick_info in ticker_map.values():
        ticker_count[tick_info["ticker"]] += 1

    registry: dict[str, Any] = {}
    for entity in entities:
        lei = entity["lei"]
        tick_info = ticker_map.get(lei, {})
        symbol = tick_info.get("symbol", lei[:8])
        exchange = tick_info.get("exchange", "ESEF")
        ticker = tick_info.get("ticker", f"{symbol}.{exchange}")

        if ticker_count[ticker] > 1:
            ticker = f"{symbol}_{lei[-4:]}.{exchange}"

        registry[ticker] = {
            "lei": lei,
            "ticker": ticker,
            "symbol": symbol,
            "exchange": exchange,
            "name": entity["name"],
            "country": entity["country"],
            "source": "esef",
        }

    logger.info("Registry built: %d entries", len(registry))
    return registry, filing_urls
