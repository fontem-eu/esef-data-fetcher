"""Unit tests for filing_fetcher.py — no network calls."""
from __future__ import annotations

from datetime import date
from unittest.mock import MagicMock, patch

from src.filing_fetcher import _download_facts, _fiscal_year, _parse_date, fetch_entity_summaries


# ---------------------------------------------------------------------------
# _parse_date
# ---------------------------------------------------------------------------

def test_parse_date_iso_string():
    """ISO date string is parsed to a date object."""
    assert _parse_date("2023-12-31") == date(2023, 12, 31)


def test_parse_date_trims_to_10_chars():
    """Datetime strings are trimmed to the date portion."""
    assert _parse_date("2023-12-31T00:00:00") == date(2023, 12, 31)


def test_parse_date_none_returns_none():
    """None input returns None."""
    assert _parse_date(None) is None


def test_parse_date_empty_string_returns_none():
    """Empty string returns None."""
    assert _parse_date("") is None


def test_parse_date_invalid_returns_none():
    """Invalid date string returns None."""
    assert _parse_date("not-a-date") is None


# ---------------------------------------------------------------------------
# _fiscal_year
# ---------------------------------------------------------------------------

def test_fiscal_year_returns_year_of_date():
    """Fiscal year is the year component of the end date."""
    assert _fiscal_year(date(2022, 12, 31)) == 2022
    assert _fiscal_year(date(2023, 3, 31)) == 2023


# ---------------------------------------------------------------------------
# _download_facts
# ---------------------------------------------------------------------------

def test_download_facts_returns_facts_dict():
    """Returns the 'facts' dict from the JSON response."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {"facts": {"ifrs-full:Revenue": {"value": 1000}}}

    with patch("src.filing_fetcher.requests.get", return_value=mock_resp):
        result = _download_facts("https://example.com/report.json", timeout=10)

    assert result == {"ifrs-full:Revenue": {"value": 1000}}


def test_download_facts_returns_empty_dict_when_no_facts_key():
    """Returns empty dict when 'facts' key is absent."""
    mock_resp = MagicMock()
    mock_resp.status_code = 200
    mock_resp.json.return_value = {}

    with patch("src.filing_fetcher.requests.get", return_value=mock_resp):
        result = _download_facts("https://example.com/report.json", timeout=10)

    assert result == {}


def test_download_facts_returns_none_on_http_error():
    """Returns None when an HTTP error is raised."""
    mock_resp = MagicMock()
    mock_resp.raise_for_status.side_effect = Exception("HTTP 500")

    with patch("src.filing_fetcher.requests.get", return_value=mock_resp):
        result = _download_facts("https://example.com/report.json", timeout=10)

    assert result is None


def test_download_facts_returns_none_on_network_error():
    """Returns None on a network exception."""
    with patch("src.filing_fetcher.requests.get", side_effect=ConnectionError("timeout")):
        result = _download_facts("https://example.com/report.json", timeout=10)

    assert result is None


# ---------------------------------------------------------------------------
# fetch_entity_summaries
# ---------------------------------------------------------------------------

def _make_filing_ref(end_date: str, json_url: str = "https://example.com/r.json",
                     filing_index: str = "idx") -> dict:
    return {"end_date": end_date, "json_url": json_url, "filing_index": filing_index}


def test_fetch_entity_summaries_returns_summaries():
    """Returns one summary per unique fiscal year."""
    facts = {"ifrs-full:Revenue": {"value": 1000}}
    summary_data = {"revenue": 1000}

    with patch("src.filing_fetcher._download_facts", return_value=facts), \
         patch("src.filing_fetcher.extract_summary", return_value=summary_data):
        result = fetch_entity_summaries(
            "LEI001",
            [_make_filing_ref("2022-12-31"), _make_filing_ref("2021-12-31")],
        )

    assert len(result) == 2
    years = {r["year"] for r in result}
    assert years == {2022, 2021}


def test_fetch_entity_summaries_deduplicates_fiscal_year():
    """Only one summary per fiscal year — amendments are skipped."""
    facts = {"ifrs-full:Revenue": {"value": 1000}}
    summary_data = {"revenue": 1000}

    with patch("src.filing_fetcher._download_facts", return_value=facts), \
         patch("src.filing_fetcher.extract_summary", return_value=summary_data):
        result = fetch_entity_summaries(
            "LEI001",
            [
                _make_filing_ref("2022-12-31", json_url="https://example.com/a.json"),
                _make_filing_ref("2022-12-31", json_url="https://example.com/b.json"),
            ],
        )

    assert len(result) == 1


def test_fetch_entity_summaries_skips_all_none_facts():
    """Summaries where all values are None are excluded."""
    with patch("src.filing_fetcher._download_facts", return_value={}), \
         patch("src.filing_fetcher.extract_summary",
               return_value={"revenue": None, "net_income": None}):
        result = fetch_entity_summaries("LEI001", [_make_filing_ref("2022-12-31")])

    assert not result


def test_fetch_entity_summaries_skips_missing_end_date():
    """Filing refs without an end_date are skipped."""
    with patch("src.filing_fetcher._download_facts") as mock_dl:
        result = fetch_entity_summaries("LEI001", [{"json_url": "https://example.com/r.json"}])

    mock_dl.assert_not_called()
    assert not result


def test_fetch_entity_summaries_skips_download_failure():
    """Filing refs where _download_facts returns None are skipped."""
    with patch("src.filing_fetcher._download_facts", return_value=None):
        result = fetch_entity_summaries("LEI001", [_make_filing_ref("2022-12-31")])

    assert not result


def test_fetch_entity_summaries_empty_refs():
    """Empty filing_refs list returns empty list."""
    result = fetch_entity_summaries("LEI001", [])
    assert not result
