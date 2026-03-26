"""Unit tests for ifrs_mapper — no network calls."""
from __future__ import annotations

from datetime import date

import pytest

from src.ifrs_mapper import extract_summary, _period_end_str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_fact(concept: str, period: str, value: str, unit: str = "iso4217:EUR") -> dict:
    return {
        "value": value,
        "dimensions": {
            "concept": concept,
            "entity": "scheme:TEST_LEI",
            "period": period,
            "unit": unit,
        },
    }


def _make_facts(items: list[tuple[str, str, str]]) -> dict:
    return {f"fact_{i}": _make_fact(c, p, v) for i, (c, p, v) in enumerate(items)}


# ---------------------------------------------------------------------------
# _period_end_str
# ---------------------------------------------------------------------------

def test_period_end_str_dec31():
    assert _period_end_str(date(2022, 12, 31)) == "2023-01-01T00:00:00"


def test_period_end_str_mar31():
    assert _period_end_str(date(2023, 3, 31)) == "2023-04-01T00:00:00"


# ---------------------------------------------------------------------------
# extract_summary — happy path
# ---------------------------------------------------------------------------

FILING_END = date(2022, 12, 31)
PERIOD_END = "2023-01-01T00:00:00"
PERIOD_START = "2022-01-01T00:00:00"
DURATION = f"{PERIOD_START}/{PERIOD_END}"


@pytest.fixture
def full_facts():
    return _make_facts([
        ("ifrs-full:Revenue",                              DURATION, "10000000000"),
        ("ifrs-full:GrossProfit",                          DURATION, "4000000000"),
        ("ifrs-full:ProfitLossFromOperatingActivities",    DURATION, "2000000000"),
        ("ifrs-full:ProfitLossAttributableToOwnersOfParent", DURATION, "1500000000"),
        ("ifrs-full:EarningsPerShareBasic",                DURATION, "3.75"),
        ("ifrs-full:CashFlowsFromUsedInOperatingActivities", DURATION, "2200000000"),
        ("ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
                                                           DURATION, "500000000"),
        ("ifrs-full:FinanceCosts",                         DURATION, "80000000"),
        ("ifrs-full:IncomeTaxExpenseContinuingOperations", DURATION, "350000000"),
        ("ifrs-full:DepreciationAndAmortisationExpense",   DURATION, "400000000"),
        ("ifrs-full:WeightedAverageNumberOfSharesOutstandingBasic", DURATION, "400000000"),
        # Balance sheet (instant)
        ("ifrs-full:Assets",           PERIOD_END, "20000000000"),
        ("ifrs-full:Liabilities",      PERIOD_END, "8000000000"),
        ("ifrs-full:EquityAttributableToOwnersOfParent", PERIOD_END, "12000000000"),
        ("ifrs-full:CurrentAssets",    PERIOD_END, "6000000000"),
        ("ifrs-full:CurrentLiabilities", PERIOD_END, "3000000000"),
        ("ifrs-full:Inventories",      PERIOD_END, "1500000000"),
        ("ifrs-full:CashAndCashEquivalents", PERIOD_END, "800000000"),
        ("ifrs-full:LongtermBorrowings", PERIOD_END, "2500000000"),
    ])


def test_extract_revenue(full_facts):
    s = extract_summary(full_facts, FILING_END)
    assert s["revenue"] == 10_000_000_000.0


def test_extract_gross_profit(full_facts):
    s = extract_summary(full_facts, FILING_END)
    assert s["gross_profit"] == 4_000_000_000.0


def test_extract_net_income(full_facts):
    s = extract_summary(full_facts, FILING_END)
    assert s["net_income"] == 1_500_000_000.0


def test_extract_eps(full_facts):
    s = extract_summary(full_facts, FILING_END)
    assert s["eps"] == pytest.approx(3.75)


def test_extract_capex_is_negative(full_facts):
    """CapEx should be stored as negative (outflow convention)."""
    s = extract_summary(full_facts, FILING_END)
    assert s["capex"] == -500_000_000.0


def test_extract_free_cashflow_derived(full_facts):
    s = extract_summary(full_facts, FILING_END)
    # 2_200_000_000 + (-500_000_000)
    assert s["free_cashflow"] == pytest.approx(1_700_000_000.0)


def test_extract_balance_sheet(full_facts):
    s = extract_summary(full_facts, FILING_END)
    assert s["total_assets"] == 20_000_000_000.0
    assert s["total_liabilities"] == 8_000_000_000.0
    assert s["equity"] == 12_000_000_000.0
    assert s["current_assets"] == 6_000_000_000.0
    assert s["current_liabilities"] == 3_000_000_000.0
    assert s["inventory"] == 1_500_000_000.0
    assert s["cash_and_equivalents"] == 800_000_000.0
    assert s["long_term_debt"] == 2_500_000_000.0


def test_extract_shares(full_facts):
    s = extract_summary(full_facts, FILING_END)
    assert s["shares_outstanding"] == 400_000_000.0


# ---------------------------------------------------------------------------
# extract_summary — missing / fallback concepts
# ---------------------------------------------------------------------------

def test_missing_gross_profit_returns_none():
    facts = _make_facts([
        ("ifrs-full:Revenue", DURATION, "5000000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["gross_profit"] is None
    assert s["revenue"] == 5_000_000_000.0


def test_wrong_period_not_matched():
    """Facts for a different period must not be picked up."""
    facts = _make_facts([
        ("ifrs-full:Revenue", "2021-01-01T00:00:00/2022-01-01T00:00:00", "9999999999"),
    ])
    s = extract_summary(facts, FILING_END)  # FILING_END = 2022-12-31
    assert s["revenue"] is None


def test_dimensioned_facts_excluded():
    """Facts with extra dimensions (e.g. segment) must be ignored."""
    fact = {
        "value": "1234",
        "dimensions": {
            "concept": "ifrs-full:Revenue",
            "entity": "scheme:LEI",
            "period": DURATION,
            "unit": "iso4217:EUR",
            "ifrs-full:SegmentsAxis": "some:Segment",
        },
    }
    s = extract_summary({"f0": fact}, FILING_END)
    assert s["revenue"] is None


def test_fallback_net_income_to_profit_loss():
    """ProfitLoss is the fallback when ProfitLossAttributableToOwnersOfParent absent."""
    facts = _make_facts([
        ("ifrs-full:ProfitLoss", DURATION, "800000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["net_income"] == 800_000_000.0


def test_shares_fallback_to_instant():
    """shares_outstanding falls back to NumberOfSharesOutstanding (instant)."""
    facts = _make_facts([
        ("ifrs-full:NumberOfSharesOutstanding", PERIOD_END, "300000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["shares_outstanding"] == 300_000_000.0


def test_empty_facts_returns_all_none():
    s = extract_summary({}, FILING_END)
    assert all(v is None for v in s.values())


def test_non_december_fiscal_year():
    """March 31 fiscal year end should correctly match its period strings."""
    mar_end = date(2023, 3, 31)
    period_end = "2023-04-01T00:00:00"
    duration = f"2022-04-01T00:00:00/{period_end}"

    facts = _make_facts([
        ("ifrs-full:Revenue", duration, "7000000000"),
        ("ifrs-full:Assets",  period_end, "15000000000"),
    ])
    s = extract_summary(facts, mar_end)
    assert s["revenue"] == 7_000_000_000.0
    assert s["total_assets"] == 15_000_000_000.0
