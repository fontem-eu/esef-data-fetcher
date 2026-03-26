"""Unit tests for storage helpers."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src.storage import write_registry, read_registry, write_summary, write_metadata


@pytest.fixture
def tmp(tmp_path):
    return tmp_path


def test_write_and_read_registry(tmp):
    registry = {
        "ASML.AS": {"lei": "LEI001", "name": "ASML Holding", "country": "NL"},
    }
    write_registry(registry, tmp)
    loaded = read_registry(tmp)
    assert loaded["ASML.AS"]["lei"] == "LEI001"


def test_read_registry_missing_returns_empty(tmp):
    assert read_registry(tmp) == {}


def test_write_summary_creates_file(tmp):
    meta = {"lei": "LEI001", "ticker": "ASML.AS", "name": "ASML", "country": "NL", "source": "esef"}
    filings = [{"year": 2022, "revenue": 21_000_000_000}]
    write_summary("ASML.AS", meta, filings, tmp)
    path = tmp / "summaries" / "ASML.AS.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert data["filings"][0]["revenue"] == 21_000_000_000


def test_write_summary_sanitises_slash_in_ticker(tmp):
    meta = {"lei": "L1", "ticker": "A/B.DE", "name": "Test", "country": "DE", "source": "esef"}
    write_summary("A/B.DE", meta, [], tmp)
    assert (tmp / "summaries" / "A_B.DE.json").exists()


def test_write_metadata(tmp):
    write_metadata(tmp, total_entities=100, fetched=90, skipped=5, errors=5, elapsed_seconds=42.3)
    data = json.loads((tmp / "metadata.json").read_text())
    assert data["fetched"] == 90
    assert data["elapsed_seconds"] == 42.3
