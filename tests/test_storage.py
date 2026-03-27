"""Unit tests for storage helpers."""
from __future__ import annotations

import json

import pytest

from src.storage import write_registry, read_registry, write_summary, write_metadata


@pytest.fixture(name="tmp_dir")
def fixture_tmp_dir(tmp_path):
    """Return a temporary directory path for each test."""
    return tmp_path


def test_write_and_read_registry(tmp_dir):
    """Written registry can be read back correctly."""
    registry = {
        "ASML.AS": {"lei": "LEI001", "name": "ASML Holding", "country": "NL"},
    }
    write_registry(registry, tmp_dir)
    loaded = read_registry(tmp_dir)
    assert loaded["ASML.AS"]["lei"] == "LEI001"


def test_read_registry_missing_returns_empty(tmp_dir):
    """Reading a non-existent registry returns an empty dict."""
    assert read_registry(tmp_dir) == {}


def test_write_summary_creates_file(tmp_dir):
    """write_summary creates a JSON file in the summaries subdirectory."""
    meta = {"lei": "LEI001", "ticker": "ASML.AS", "name": "ASML", "country": "NL", "source": "esef"}
    filings = [{"year": 2022, "revenue": 21_000_000_000}]
    write_summary("ASML.AS", meta, filings, tmp_dir)
    path = tmp_dir / "summaries" / "ASML.AS.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["filings"][0]["revenue"] == 21_000_000_000


def test_write_summary_sanitises_slash_in_ticker(tmp_dir):
    """Slashes in ticker names are replaced with underscores in the filename."""
    meta = {"lei": "L1", "ticker": "A/B.DE", "name": "Test", "country": "DE", "source": "esef"}
    write_summary("A/B.DE", meta, [], tmp_dir)
    assert (tmp_dir / "summaries" / "A_B.DE.json").exists()


def test_write_metadata(tmp_dir):
    """write_metadata writes correct values to metadata.json."""
    write_metadata(
        tmp_dir, total_entities=100, fetched=90, skipped=5, errors=5, elapsed_seconds=42.3
    )
    data = json.loads((tmp_dir / "metadata.json").read_text())
    assert data["fetched"] == 90
    assert data["elapsed_seconds"] == 42.3
