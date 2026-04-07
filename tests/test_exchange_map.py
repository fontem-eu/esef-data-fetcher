"""Unit tests for exchange_map helpers — no network calls."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from src.exchange_map import (
    COUNTRY_TO_EXCHANGE,
    _gleif_get_isins,
    _name_to_symbol,
    _normalize_exchange,
    _openfigi_by_isin,
    resolve_tickers,
)


def test_name_to_symbol_simple():
    """Simple name with NV suffix strips to first word."""
    assert _name_to_symbol("ASML Holding N.V.") == "ASML"


def test_name_to_symbol_strips_legal_suffix():
    """Aktiengesellschaft (AG) is stripped and result is truncated."""
    assert _name_to_symbol("Volkswagen Aktiengesellschaft") == "VOLKSWAGEN"[:8]


def test_name_to_symbol_long_name_truncated():
    """Symbols are capped at 8 characters."""
    sym = _name_to_symbol("A Very Long Company Name AG")
    assert len(sym) <= 8


def test_name_to_symbol_nv_suffix():
    """N.V. suffix is removed, leaving the company name."""
    assert _name_to_symbol("Signify N.V.") == "SIGNIFY"


def test_normalize_exchange_uses_alias():
    """OpenFIGI exchCodes are translated via the alias table."""
    assert _normalize_exchange("GY", "DE") == "DE"
    assert _normalize_exchange("FP", "FR") == "PA"
    assert _normalize_exchange("LN", "GB") == "L"


def test_normalize_exchange_falls_back_to_country():
    """Empty exchCode falls back to COUNTRY_TO_EXCHANGE."""
    assert _normalize_exchange("", "NL") == "AS"
    assert _normalize_exchange("", "DE") == "DE"


def test_normalize_exchange_unknown_country():
    """Unknown country code falls back to 'ESEF'."""
    assert _normalize_exchange("", "ZZ") == "ESEF"


def test_country_to_exchange_has_major_markets():
    """All major EU markets are present in COUNTRY_TO_EXCHANGE."""
    for country in ("NL", "DE", "FR", "GB", "IT", "ES"):
        assert country in COUNTRY_TO_EXCHANGE


def test_resolve_tickers_no_openfigi():
    """Without OpenFIGI, tickers are null (no FIGI resolution possible)."""
    entities = [
        {"lei": "LEI001", "name": "ASML Holding N.V.", "country": "NL"},
        {"lei": "LEI002", "name": "SAP SE", "country": "DE"},
    ]
    result = resolve_tickers(entities, use_openfigi=False)
    assert result["LEI001"]["ticker"] is None
    assert result["LEI002"]["ticker"] is None


def test_resolve_tickers_openfigi_hit():
    """GLEIF returns ISIN, OpenFIGI resolves to ASML on Amsterdam."""
    entities = [{"lei": "LEI001", "name": "ASML Holding N.V.", "country": "NL"}]

    gleif_response = MagicMock()
    gleif_response.status_code = 200
    gleif_response.json.return_value = {
        "data": [{"attributes": {"isin": "NL0010273215"}}]
    }

    openfigi_response = MagicMock()
    openfigi_response.status_code = 200
    openfigi_response.json.return_value = [{
        "data": [{"ticker": "ASML", "exchCode": "NA", "securityType": "Common Stock"}]
    }]

    with patch("src.exchange_map.requests.get", return_value=gleif_response), \
         patch("src.exchange_map.requests.post", return_value=openfigi_response):
        result = resolve_tickers(entities, use_openfigi=True, batch_size=10)

    assert result["LEI001"]["symbol"] == "ASML"
    assert result["LEI001"]["exchange"] == "AS"   # "NA" → "AS" via alias
    assert result["LEI001"]["ticker"] == "ASML.AS"


def test_resolve_tickers_openfigi_miss_returns_null():
    """GLEIF returns empty data and FIGI has no match — ticker is null."""
    entities = [{"lei": "LEI001", "name": "Signify N.V.", "country": "NL"}]

    gleif_response = MagicMock()
    gleif_response.status_code = 200
    gleif_response.json.return_value = {"data": []}  # no ISINs

    openfigi_response = MagicMock()
    openfigi_response.status_code = 200
    openfigi_response.json.return_value = [{"data": []}]

    with patch("src.exchange_map.requests.get", return_value=gleif_response), \
         patch("src.exchange_map.requests.post", return_value=openfigi_response):
        result = resolve_tickers(entities, use_openfigi=True, batch_size=10)

    assert result["LEI001"]["ticker"] is None
    assert result["LEI001"]["symbol"] is None
    assert result["LEI001"]["exchange"] is None


def test_gleif_returns_isin():
    """_gleif_get_isins returns the primary ISIN from GLEIF API."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {
        "data": [{"attributes": {"isin": "ES0178430E18"}}]
    }

    with patch("src.exchange_map.requests.get", return_value=mock_resp):
        result = _gleif_get_isins(["549300EEJH4FEPDBBR25"])

    assert result == {"549300EEJH4FEPDBBR25": "ES0178430E18"}


def test_gleif_404_returns_none():
    """_gleif_get_isins returns None for a 404 response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 404

    with patch("src.exchange_map.requests.get", return_value=mock_resp):
        result = _gleif_get_isins(["BADLEI"])

    assert result == {"BADLEI": None}


def test_openfigi_country_preference():
    """When multiple listings exist, prefer the one matching the country."""
    openfigi_response = MagicMock()
    openfigi_response.status_code = 200
    openfigi_response.json.return_value = [{
        "data": [
            {"ticker": "TEF", "exchCode": "SM", "securityType": "Common Stock"},
            {"ticker": "TDE", "exchCode": "GY", "securityType": "Common Stock"},
        ]
    }]

    with patch("src.exchange_map.requests.post", return_value=openfigi_response):
        result = _openfigi_by_isin([("ES0178430E18", "ES")], api_key="", timeout=10)

    # ES → MC, SM → MC via alias, so SM entry (TEF) should be chosen
    assert result["ES0178430E18"]["ticker"] == "TEF"
    assert result["ES0178430E18"]["exchange_code"] == "SM"


def test_openfigi_country_fallback_to_first():
    """When no entry matches the country, fall back to the first Common Stock."""
    openfigi_response = MagicMock()
    openfigi_response.status_code = 200
    openfigi_response.json.return_value = [{
        "data": [
            {"ticker": "TDE", "exchCode": "GY", "securityType": "Common Stock"},
            {"ticker": "TEF", "exchCode": "SM", "securityType": "Common Stock"},
        ]
    }]

    with patch("src.exchange_map.requests.post", return_value=openfigi_response):
        # Use a country with no match in either entry
        result = _openfigi_by_isin([("ES0178430E18", "FR")], api_key="", timeout=10)

    # FR → PA, neither GY→DE nor SM→MC matches PA, so first entry (TDE) is chosen
    assert result["ES0178430E18"]["ticker"] == "TDE"


def test_resolve_tickers_telefonica_gets_tef():
    """End-to-end: TELEFONICA SA should resolve to TEF.MC via GLEIF + OpenFIGI."""
    entities = [{"lei": "549300EEJH4FEPDBBR25", "name": "TELEFONICA SA", "country": "ES"}]

    gleif_response = MagicMock()
    gleif_response.status_code = 200
    gleif_response.json.return_value = {
        "data": [{"attributes": {"isin": "ES0178430E18"}}]
    }

    openfigi_response = MagicMock()
    openfigi_response.status_code = 200
    openfigi_response.json.return_value = [{
        "data": [{"ticker": "TEF", "exchCode": "SM", "securityType": "Common Stock"}]
    }]

    with patch("src.exchange_map.requests.get", return_value=gleif_response), \
         patch("src.exchange_map.requests.post", return_value=openfigi_response):
        result = resolve_tickers(entities, use_openfigi=True, batch_size=10)

    assert result["549300EEJH4FEPDBBR25"]["ticker"] == "TEF.MC"
