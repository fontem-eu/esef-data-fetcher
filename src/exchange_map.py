"""
Country → primary exchange suffix and OpenFIGI ticker lookup.

Used to build exchange-suffixed tickers (e.g. ASML.AS, SAP.DE) for EU companies
that are not in the EDGAR universe.
"""
from __future__ import annotations

import logging
import re
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
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
_GLEIF_ISIN_URL = "https://api.gleif.org/api/v1/lei-records/{lei}/isins"

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

_EQUITY_TYPES = ("Common Stock", "ETP", "Ordinary Shares")


_GLEIF_RETRY_DELAYS = (2, 5, 15)  # seconds between retries on 429


def _gleif_fetch_one(lei: str, timeout: int) -> tuple[str, list[str]]:
    """Fetch all ISINs for a single LEI from the GLEIF API.

    Retries up to 3 times on HTTP 429 (rate-limit) with increasing delays.
    Returns (lei, [isin, ...]) — empty list if none found or on error.
    """
    url = _GLEIF_ISIN_URL.format(lei=lei)
    for attempt, delay in enumerate((*_GLEIF_RETRY_DELAYS, None)):
        try:
            resp = requests.get(url, timeout=timeout)
            if resp.status_code == 404:
                return lei, []
            if resp.status_code == 429:
                if delay is not None:
                    logger.debug("GLEIF rate-limited for %s — retrying in %ds", lei, delay)
                    time.sleep(delay)
                    continue
                logger.warning(
                    "GLEIF rate-limit exceeded for LEI %s after %d attempts", lei, attempt
                )
                return lei, []
            resp.raise_for_status()
            items = resp.json().get("data", [])
            isins = [
                item["attributes"]["isin"]
                for item in items
                if item.get("attributes", {}).get("isin")
            ]
            return lei, isins
        except (requests.RequestException, KeyError) as exc:
            logger.warning("GLEIF request failed for LEI %s: %s", lei, exc)
            return lei, []
    return lei, []  # exhausted retries


def _gleif_get_isins(
    leis: list[str],
    *,
    max_workers: int = 5,
    timeout: int = 10,
) -> dict[str, str | None]:
    """
    Fetch the primary ISIN for each LEI from the GLEIF API in parallel.

    When multiple ISINs are returned by GLEIF, the first one is stored here;
    all ISINs are handled internally by ``resolve_tickers`` via
    ``_gleif_get_all_isins``.

    Returns {lei: isin_or_None}.
    """
    all_isins = _gleif_get_all_isins(leis, max_workers=max_workers, timeout=timeout)
    return {lei: (isins[0] if isins else None) for lei, isins in all_isins.items()}


def _gleif_get_all_isins(
    leis: list[str],
    *,
    max_workers: int = 5,
    timeout: int = 10,
) -> dict[str, list[str]]:
    """
    Fetch all ISINs for each LEI from the GLEIF API in parallel.

    Returns {lei: [isin, ...]} — values may be empty lists.
    """
    result: dict[str, list[str]] = {}
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(_gleif_fetch_one, lei, timeout): lei for lei in leis}
        for future in as_completed(futures):
            lei_result, isins = future.result()
            result[lei_result] = isins
    return result


def _pick_best_entry(entries: list[dict], preferred_exchange: str) -> dict | None:
    """
    From a list of OpenFIGI entries, pick the best equity entry.

    Prefers the entry whose exchCode (normalized via _EXCH_ALIASES) matches
    ``preferred_exchange``. Falls back to the first equity entry.
    """
    common_stocks = [e for e in entries if e.get("securityType") in _EQUITY_TYPES]
    if not common_stocks:
        return None
    for entry in common_stocks:
        exch_code = entry.get("exchCode", "")
        normalized = _EXCH_ALIASES.get(exch_code.upper(), exch_code.upper())
        if normalized == preferred_exchange:
            return entry
    return common_stocks[0]


def _openfigi_by_isin(
    isin_country_pairs: list[tuple[str, str]],
    api_key: str,
    timeout: int,
) -> dict[str, dict[str, str]]:
    """
    Look up tickers from OpenFIGI using ISIN identifiers.

    ``isin_country_pairs`` is a list of (isin, country_code) tuples.

    For each ISIN result, prefers the ``Common Stock`` entry whose exchCode
    (normalized via ``_EXCH_ALIASES``) matches ``COUNTRY_TO_EXCHANGE[country]``.
    If no country match is found, takes the first ``Common Stock`` entry.

    Returns {isin: {"ticker": ..., "exchange_code": ...}}.
    """
    if not isin_country_pairs:
        return {}

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["X-OPENFIGI-APIKEY"] = api_key

    payload = [{"idType": "ID_ISIN", "idValue": isin} for isin, _ in isin_country_pairs]
    try:
        resp = requests.post(_OPENFIGI_URL, json=payload, headers=headers, timeout=timeout)
        if resp.status_code == 429:
            logger.warning("OpenFIGI rate-limited — sleeping 60s")
            time.sleep(60)
            return {}
        resp.raise_for_status()
        return _parse_openfigi_response(isin_country_pairs, resp.json())
    except requests.RequestException as exc:
        logger.warning("OpenFIGI request failed: %s", exc)
        return {}


def _parse_openfigi_response(
    isin_country_pairs: list[tuple[str, str]],
    response_data: list[dict],
) -> dict[str, dict[str, str]]:
    """Parse the raw OpenFIGI response into an {isin: figi_info} mapping."""
    results: dict[str, dict[str, str]] = {}
    for (isin, country), item in zip(isin_country_pairs, response_data):
        data = item.get("data")
        if not data:
            continue
        preferred_exchange = COUNTRY_TO_EXCHANGE.get(country, "")
        chosen = _pick_best_entry(data, preferred_exchange)
        if chosen is None:
            continue
        ticker = chosen.get("ticker")
        exch = chosen.get("exchCode") or chosen.get("marketSector")
        if ticker:
            results[isin] = {"ticker": ticker, "exchange_code": exch or ""}
    return results


_MAX_ISINS_PER_ENTITY = 4


def _build_isin_pairs(
    entities: list[dict[str, Any]],
    lei_to_isins: dict[str, list[str]],
) -> list[tuple[str, str]]:
    """Build deduplicated (isin, country) pairs from the entity list.

    Country-matching ISINs (e.g. NL* for NL entities) are placed first so
    ``_best_figi_for_lei`` finds the domestic equity listing before ADRs or
    bond ISINs.  At most ``_MAX_ISINS_PER_ENTITY`` ISINs are kept per entity
    to avoid flooding OpenFIGI with bond ISINs.
    """
    seen: set[str] = set()
    deduped: list[tuple[str, str]] = []
    for entity in entities:
        country = entity.get("country", "")
        isins = lei_to_isins.get(entity["lei"], [])
        # Domestic equity ISINs first, then others
        ordered = [i for i in isins if i.startswith(country)] + \
                  [i for i in isins if not i.startswith(country)]
        for isin in ordered[:_MAX_ISINS_PER_ENTITY]:
            if isin not in seen:
                seen.add(isin)
                deduped.append((isin, country))
    return deduped


def _best_figi_for_lei(
    isins: list[str],
    isin_to_figi: dict[str, dict[str, str]],
) -> dict[str, str]:
    """Return the first OpenFIGI hit for any of the entity's ISINs."""
    for isin in isins:
        figi = isin_to_figi.get(isin)
        if figi:
            return figi
    return {}


def resolve_tickers(  # pylint: disable=too-many-arguments
    entities: list[dict[str, Any]],
    *,
    use_openfigi: bool = True,
    api_key: str = "",
    batch_size: int = 10,
    timeout: int = 30,
    gleif_workers: int = 5,
) -> dict[str, dict[str, str]]:
    """
    For each entity (with 'lei' and 'country'), resolve:
      - ticker symbol (from OpenFIGI or name-derived)
      - exchange suffix (from OpenFIGI or country map)

    Returns {lei: {"symbol": ..., "exchange": ..., "ticker": ...}}
    where ticker = f"{symbol}.{exchange}".
    """
    isin_to_figi: dict[str, dict[str, str]] = {}
    lei_to_isins: dict[str, list[str]] = {}

    if use_openfigi:
        leis = [e["lei"] for e in entities]
        lei_to_isins = _gleif_get_all_isins(
            leis, max_workers=gleif_workers, timeout=timeout
        )
        deduped = _build_isin_pairs(entities, lei_to_isins)
        for i in range(0, len(deduped), batch_size):
            chunk = deduped[i: i + batch_size]
            isin_to_figi.update(_openfigi_by_isin(chunk, api_key, timeout))
            if not api_key:
                time.sleep(2.5)

    return _build_result(entities, lei_to_isins, isin_to_figi)


def _build_result(
    entities: list[dict[str, Any]],
    lei_to_isins: dict[str, list[str]],
    isin_to_figi: dict[str, dict[str, str]],
) -> dict[str, dict[str, str | None]]:
    """Assemble the final {lei: {symbol, exchange, ticker}} result dict.

    When OpenFIGI cannot resolve a ticker, symbol/exchange/ticker are set
    to None instead of fabricating a name-derived ticker that will never
    match a real exchange listing.
    """
    result: dict[str, dict[str, str | None]] = {}
    unresolved = 0
    for entity in entities:
        lei = entity["lei"]
        country = entity.get("country", "")

        isins = lei_to_isins.get(lei, [])
        figi = _best_figi_for_lei(isins, isin_to_figi)

        if figi.get("ticker"):
            symbol = figi["ticker"].upper().replace(" ", "")
            exchange = _normalize_exchange(figi.get("exchange_code", ""), country)
            ticker = f"{symbol}.{exchange}"
            result[lei] = {"symbol": symbol, "exchange": exchange, "ticker": ticker}
        else:
            unresolved += 1
            result[lei] = {"symbol": None, "exchange": None, "ticker": None}

    if unresolved:
        logger.info("Ticker resolution: %d of %d entities unresolved (no FIGI match)",
                     unresolved, len(entities))
    return result


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _normalize_exchange(openfigi_code: str, country: str) -> str:
    """Map an OpenFIGI exchCode to our Yahoo Finance suffix."""
    if not openfigi_code:
        return COUNTRY_TO_EXCHANGE.get(country, "ESEF")
    return _EXCH_ALIASES.get(openfigi_code.upper(), openfigi_code.upper())


def _name_to_symbol(name: str) -> str:
    """Derive a short symbol from a company legal name."""
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
