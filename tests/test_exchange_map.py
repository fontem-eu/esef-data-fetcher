"""Unit tests for exchange_map helpers — no network calls."""
from __future__ import annotations

from unittest.mock import patch

from src.exchange_map import _name_to_symbol, _normalize_exchange, resolve_tickers, COUNTRY_TO_EXCHANGE


def test_name_to_symbol_simple():
    assert _name_to_symbol("ASML Holding N.V.") == "ASML"


def test_name_to_symbol_strips_legal_suffix():
    assert _name_to_symbol("Volkswagen Aktiengesellschaft") == "VOLKSWAGEN"[:8]


def test_name_to_symbol_long_name_truncated():
    sym = _name_to_symbol("A Very Long Company Name AG")
    assert len(sym) <= 8


def test_name_to_symbol_nv_suffix():
    assert _name_to_symbol("Signify N.V.") == "SIGNIFY"


def test_normalize_exchange_uses_alias():
    assert _normalize_exchange("GY", "DE") == "DE"
    assert _normalize_exchange("FP", "FR") == "PA"
    assert _normalize_exchange("LN", "GB") == "L"


def test_normalize_exchange_falls_back_to_country():
    assert _normalize_exchange("", "NL") == "AS"
    assert _normalize_exchange("", "DE") == "DE"


def test_normalize_exchange_unknown_country():
    assert _normalize_exchange("", "ZZ") == "ESEF"


def test_country_to_exchange_has_major_markets():
    for country in ("NL", "DE", "FR", "GB", "IT", "ES"):
        assert country in COUNTRY_TO_EXCHANGE


def test_resolve_tickers_no_openfigi():
    entities = [
        {"lei": "LEI001", "name": "ASML Holding N.V.", "country": "NL"},
        {"lei": "LEI002", "name": "SAP SE", "country": "DE"},
    ]
    result = resolve_tickers(entities, use_openfigi=False)
    assert result["LEI001"]["ticker"] == "ASML.AS"
    assert result["LEI002"]["ticker"] == "SAP.DE"


def test_resolve_tickers_openfigi_hit():
    entities = [{"lei": "LEI001", "name": "ASML Holding N.V.", "country": "NL"}]
    fake_response = [{
        "data": [{"ticker": "ASML", "exchCode": "NA", "securityType": "Common Stock"}]
    }]
    with patch("src.exchange_map.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = fake_response
        result = resolve_tickers(entities, use_openfigi=True, batch_size=10)

    assert result["LEI001"]["symbol"] == "ASML"
    assert result["LEI001"]["exchange"] == "AS"   # "NA" → "AS" via alias
    assert result["LEI001"]["ticker"] == "ASML.AS"


def test_resolve_tickers_openfigi_miss_falls_back():
    entities = [{"lei": "LEI001", "name": "Signify N.V.", "country": "NL"}]
    fake_response = [{"data": []}]  # no hit
    with patch("src.exchange_map.requests.post") as mock_post:
        mock_post.return_value.status_code = 200
        mock_post.return_value.json.return_value = fake_response
        result = resolve_tickers(entities, use_openfigi=True, batch_size=10)

    assert result["LEI001"]["exchange"] == "AS"
    assert result["LEI001"]["ticker"] == "SIGNIFY.AS"
