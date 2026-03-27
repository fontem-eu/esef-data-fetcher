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
    """December 31 maps to January 1 the following year."""
    assert _period_end_str(date(2022, 12, 31)) == "2023-01-01T00:00:00"


def test_period_end_str_mar31():
    """March 31 maps to April 1 the same year."""
    assert _period_end_str(date(2023, 3, 31)) == "2023-04-01T00:00:00"


# ---------------------------------------------------------------------------
# extract_summary — happy path
# ---------------------------------------------------------------------------

FILING_END = date(2022, 12, 31)
PERIOD_END = "2023-01-01T00:00:00"
PERIOD_START = "2022-01-01T00:00:00"
DURATION = f"{PERIOD_START}/{PERIOD_END}"


@pytest.fixture(name="full_facts")
def fixture_full_facts():
    """A complete set of IFRS facts covering all supported concepts."""
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
    """Revenue is extracted correctly from IFRS facts."""
    s = extract_summary(full_facts, FILING_END)
    assert s["revenue"] == 10_000_000_000.0


def test_extract_gross_profit(full_facts):
    """Gross profit is extracted correctly from IFRS facts."""
    s = extract_summary(full_facts, FILING_END)
    assert s["gross_profit"] == 4_000_000_000.0


def test_extract_net_income(full_facts):
    """Net income attributable to owners is extracted correctly."""
    s = extract_summary(full_facts, FILING_END)
    assert s["net_income"] == 1_500_000_000.0


def test_extract_eps(full_facts):
    """Earnings per share is extracted and approximately correct."""
    s = extract_summary(full_facts, FILING_END)
    assert s["eps"] == pytest.approx(3.75)


def test_extract_capex_is_negative(full_facts):
    """CapEx should be stored as negative (outflow convention)."""
    s = extract_summary(full_facts, FILING_END)
    assert s["capex"] == -500_000_000.0


def test_extract_free_cashflow_derived(full_facts):
    """Free cash flow is derived as operating CF + capex."""
    s = extract_summary(full_facts, FILING_END)
    # 2_200_000_000 + (-500_000_000)
    assert s["free_cashflow"] == pytest.approx(1_700_000_000.0)


def test_extract_balance_sheet(full_facts):
    """All balance sheet items are extracted correctly."""
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
    """Shares outstanding is extracted from weighted average shares."""
    s = extract_summary(full_facts, FILING_END)
    assert s["shares_outstanding"] == 400_000_000.0


# ---------------------------------------------------------------------------
# extract_summary — missing / fallback concepts
# ---------------------------------------------------------------------------

def test_missing_gross_profit_returns_none():
    """Missing gross profit concept returns None without affecting other fields."""
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
    """Empty facts dict results in all summary values being None."""
    s = extract_summary({}, FILING_END)
    assert all(v is None for v in s.values())


# ---------------------------------------------------------------------------
# Fallback concepts added for broader EU coverage
# ---------------------------------------------------------------------------

def test_capex_fallback_payments_for_ppe():
    """ifrs-full:PaymentsForPropertyPlantAndEquipment → capex (negated)."""
    facts = _make_facts([
        ("ifrs-full:PaymentsForPropertyPlantAndEquipment", DURATION, "700000000"),
        ("ifrs-full:CashFlowsFromUsedInOperatingActivities", DURATION, "2000000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["capex"] == -700_000_000.0
    assert s["free_cashflow"] == pytest.approx(1_300_000_000.0)


def test_capex_fallback_ppe_and_intangibles():
    """ifrs-full:PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets → capex (negated)."""
    facts = _make_facts([
        ("ifrs-full:PurchaseOfPropertyPlantAndEquipmentAndIntangibleAssets", DURATION, "900000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["capex"] == -900_000_000.0


def test_capex_fallback_investing_cf_negative():
    """CashFlowsFromUsedInInvestingActivities (negative) is used as last-resort capex."""
    facts = _make_facts([
        ("ifrs-full:CashFlowsFromUsedInInvestingActivities", DURATION, "-1017000000"),
        ("ifrs-full:CashFlowsFromUsedInOperatingActivities", DURATION, "2527000000"),
    ])
    s = extract_summary(facts, FILING_END)
    # Investing CF is already negative — stored as-is
    assert s["capex"] == pytest.approx(-1_017_000_000.0)
    assert s["free_cashflow"] == pytest.approx(2_527_000_000.0 + (-1_017_000_000.0))


def test_capex_fallback_investing_cf_positive_not_used():
    """Positive CashFlowsFromUsedInInvestingActivities (bank-like) must NOT become capex."""
    facts = _make_facts([
        ("ifrs-full:CashFlowsFromUsedInInvestingActivities", DURATION, "7304000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["capex"] is None
    assert s["free_cashflow"] is None


def test_capex_specific_tag_preferred_over_investing_cf():
    """A specific PPE tag must win over the total investing-CF fallback."""
    facts = _make_facts([
        ("ifrs-full:PurchaseOfPropertyPlantAndEquipment", DURATION, "400000000"),
        ("ifrs-full:CashFlowsFromUsedInInvestingActivities", DURATION, "-1500000000"),
    ])
    s = extract_summary(facts, FILING_END)
    # Specific tag takes priority; result is negated specific tag, not the investing CF
    assert s["capex"] == -400_000_000.0


def test_income_tax_fallback_current_tax_expense():
    """ifrs-full:CurrentTaxExpenseIncome → income_tax_expense fallback."""
    facts = _make_facts([
        ("ifrs-full:CurrentTaxExpenseIncome", DURATION, "280000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["income_tax_expense"] == 280_000_000.0


def test_da_fallback_ppe_specific():
    """AdjustmentsForDepreciationAmortisationAndImpairmentLossOfPropertyPlantAndEquipment → D&A."""
    ppe_da_concept = (
        "ifrs-full:AdjustmentsForDepreciationAmortisationAndImpairment"
        "LossOfPropertyPlantAndEquipment"
    )
    facts = _make_facts([(ppe_da_concept, DURATION, "550000000")])
    s = extract_summary(facts, FILING_END)
    assert s["depreciation_amortization"] == 550_000_000.0


def test_operating_income_fallback_profit_before_tax():
    """ifrs-full:ProfitBeforeTax → operating_income when EBIT concepts absent."""
    facts = _make_facts([
        ("ifrs-full:ProfitBeforeTax", DURATION, "1800000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["operating_income"] == 1_800_000_000.0


def test_operating_income_fallback_profit_loss_before_income_tax():
    """ifrs-full:ProfitLossBeforeIncomeTax → operating_income fallback."""
    facts = _make_facts([
        ("ifrs-full:ProfitLossBeforeIncomeTax", DURATION, "2100000000"),
    ])
    s = extract_summary(facts, FILING_END)
    assert s["operating_income"] == 2_100_000_000.0


def test_operating_income_preferred_over_profit_before_tax():
    """ProfitLossFromOperatingActivities must win over ProfitBeforeTax."""
    facts = _make_facts([
        ("ifrs-full:ProfitLossFromOperatingActivities", DURATION, "2000000000"),
        ("ifrs-full:ProfitBeforeTax", DURATION, "1700000000"),  # lower because finance costs
    ])
    s = extract_summary(facts, FILING_END)
    assert s["operating_income"] == 2_000_000_000.0


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
