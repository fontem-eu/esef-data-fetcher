"""
Country → primary exchange suffix and OpenFIGI ticker lookup.

Used to build exchange-suffixed tickers (e.g. ASML.AS, SAP.DE) for EU companies
that are not in the EDGAR universe.
"""
from __future__ import annotations

import logging
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

# ISO 3166-1 alpha-2 country code → Yahoo Finance / common exchange suffix
# Primary listing exchange for each country's regulated market.
COUNTRY_TO_EXCHANGE: dict[str, str] = {
    "AT": "VI",    # Vienna Stock Exchange
    "BE": "BR",    # Euronext Brussels
    "BG": "BUL",   # Bulgarian Stock Exchange
    "CY": "CSE",   # Cyprus Stock Exchange
    "CZ": "PR",    # Prague Stock Exchange
    "DE": "DE",    # Frankfurt / XETRA
    "DK": "CO",    # Nasdaq Copenhagen
    "EE": "TLN",   # Nasdaq Tallinn
    "ES": "MC",    # Bolsa de Madrid
    "FI": "HE",    # Nasdaq Helsinki
    "FR": "PA",    # Euronext Paris
    "GB": "L",     # London Stock Exchange
    "GR": "AT",    # Athens Stock Exchange
    "HR": "ZAG",   # Zagreb Stock Exchange
    "HU": "BD",    # Budapest Stock Exchange
    "IE": "IR",    # Euronext Dublin
    "IS": "IC",    # Nasdaq Iceland
    "IT": "MI",    # Borsa Italiana / Euronext Milan
    "LT": "VS",    # Nasdaq Vilnius
    "LU": "LU",    # Luxembourg Stock Exchange
    "LV": "RIG",   # Nasdaq Riga
    "MT": "MSE",   # Malta Stock Exchange
    "NL": "AS",    # Euronext Amsterdam
    "NO": "OL",    # Oslo Stock Exchange
    "PL": "WA",    # Warsaw Stock Exchange
    "PT": "LS",    # Euronext Lisbon
    "RO": "RO",    # Bucharest Stock Exchange
    "SE": "ST",    # Nasdaq Stockholm
    "SI": "LJSE",  # Ljubljana Stock Exchange
    "SK": "BSSE",  # Bratislava Stock Exchange
    "TR": "IS",    # Borsa Istanbul
    "UA": "PFTS",  # PFTS Ukraine
}

_OPENFIGI_URL = "https://api.openfigi.com/v3/mapping"


def _openfigi_batch(
    leis: list[str], api_key: str, timeout: int
) -> dict[str, dict[str, str]]:
    """
    Call OpenFIGI for a batch of LEIs.
    Returns {lei: {"ticker": ..., "exchange_code": ...}} for successful hits.
    """
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key

    payload = [{"idType": "ID_LEI", "idValue": lei} for lei in leis]
    try:
        resp = requests.post(_OPENFIGI_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            logger.warning("OpenFIGI rate-limited — sleeping 60s")
            time.sleep(60)
            return {}
        resp.raise_for_status()
        results: dict[str, dict[str, str]] = {}
        for lei, item in zip(leis, resp.json()):
            data = item.get("data")
            if not data:
                continue
            # Prefer equity instruments on a recognized exchange
            for entry in data:
                if entry.get("securityType") in ("Common Stock", "ETP", "Ordinary Shares"):
                    ticker = entry.get("ticker")
                    exch = entry.get("exchCode") or entry.get("marketSector")
                    if ticker:
                        results[lei] = {"ticker": ticker, "exchange_code": exch or ""}
                        break
        return results
    except requests.RequestException as exc:
        logger.warning("OpenFIGI request failed: %s", exc)
        return {}


def resolve_tickers(
    entities: list[dict[str, Any]],
    *,
    use_openfigi: bool = True,
    api_key: str = "",
    batch_size: int = 10,
    timeout: int = 30,
) -> dict[str, dict[str, str]]:
    """
    For each entity (with 'lei' and 'country'), resolve:
      - ticker symbol (from OpenFIGI or name-derived)
      - exchange suffix (from OpenFIGI or country map)

    Returns {lei: {"symbol": ..., "exchange": ..., "ticker": ...}}
    where ticker = f"{symbol}.{exchange}".
    """
    result: dict[str, dict[str, str]] = {}
    figi_map: dict[str, dict[str, str]] = {}

    if use_openfigi:
        leis = [e["lei"] for e in entities]
        for i in range(0, len(leis), batch_size):
            chunk = leis[i : i + batch_size]
            hits = _openfigi_batch(chunk, api_key, timeout)
            figi_map.update(hits)
            # Respect free-tier rate limit (max 25 req/min without key)
            if not api_key:
                time.sleep(2.5)

    for entity in entities:
        lei = entity["lei"]
        country = entity.get("country", "")
        name = entity.get("name", "")

        figi = figi_map.get(lei, {})
        if figi.get("ticker"):
            symbol = figi["ticker"].upper().replace(" ", "")
            # OpenFIGI exchCode (e.g. "AS", "GY") — map common aliases
            exchange = _normalize_exchange(figi.get("exchange_code", ""), country)
        else:
            symbol = _name_to_symbol(name)
            exchange = COUNTRY_TO_EXCHANGE.get(country, "ESEF")

        ticker = f"{symbol}.{exchange}"
        result[lei] = {"symbol": symbol, "exchange": exchange, "ticker": ticker}

    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# OpenFIGI exchCode → our suffix (only differs in a few cases)
_EXCH_ALIASES: dict[str, str] = {
    "GY": "DE",   # XETRA / Frankfurt
    "GF": "DE",
    "SM": "MC",   # Madrid
    "SQ": "MC",
    "IM": "MI",   # Milan
    "NA": "AS",   # Amsterdam
    "FP": "PA",   # Paris
    "LN": "L",    # London
    "NO": "OL",   # Oslo
    "SS": "ST",   # Stockholm
    "DC": "CO",   # Copenhagen
    "FH": "HE",   # Helsinki
    "ID": "IR",   # Dublin
    "PW": "WA",   # Warsaw
    "PL": "LS",   # Lisbon
}


def _normalize_exchange(openfigi_code: str, country: str) -> str:
    if not openfigi_code:
        return COUNTRY_TO_EXCHANGE.get(country, "ESEF")
    return _EXCH_ALIASES.get(openfigi_code.upper(), openfigi_code.upper())


def _name_to_symbol(name: str) -> str:
    """Derive a short symbol from a company legal name."""
    import re
    # Strip legal suffixes
    clean = re.sub(
        r"\b(N\.?V\.?|S\.?A\.?|AG|PLC|Ltd\.?|LLC|GmbH|SE|BV|AB|ASA|OYJ|A/S)\b",
        "",
        name,
        flags=re.IGNORECASE,
    )
    # Take first meaningful word
    words = [w for w in re.split(r"\W+", clean.strip()) if len(w) > 1]
    symbol = (words[0] if words else "UNKN").upper()
    return symbol[:8]  # max 8 chars
