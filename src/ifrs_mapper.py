"""
IFRS concept → summary field mapping.

Parses the xBRL-JSON report from filings.xbrl.org and extracts the 21 financial
fields that match the FinancialDataSource.get_annual_fundamentals() contract.

Period convention used by ESEF filings:
  - Duration (income / cash flow): "YYYY-01-01T00:00:00/YYYY+1-01-01T00:00:00"
  - Instant  (balance sheet):      "YYYY+1-01-01T00:00:00"

The filing's last_end_date (e.g. 2022-12-31) maps to period_end_str
"2023-01-01T00:00:00" (midnight = start of next day = end of reporting period).
"""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

# ---------------------------------------------------------------------------
# Concept priority lists — first non-null match wins
# ---------------------------------------------------------------------------

DURATION_CONCEPTS: dict[str, list[str]] = {
    "revenue": [
        "ifrs-full:Revenue",
        "ifrs-full:RevenueFromContractsWithCustomers",
        "ifrs-full:RevenueFromSaleOfGoods",
        "ifrs-full:RevenueFromRenderingOfServices",
    ],
    "gross_profit": [
        "ifrs-full:GrossProfit",
    ],
    "operating_income": [
        "ifrs-full:ProfitLossFromOperatingActivities",
        "ifrs-full:OperatingProfit",
        "ifrs-full:ProfitLossBeforeFinancingCostsAndIncomeTax",
    ],
    "net_income": [
        "ifrs-full:ProfitLossAttributableToOwnersOfParent",
        "ifrs-full:ProfitLoss",
    ],
    "eps": [
        "ifrs-full:EarningsPerShareBasic",
        "ifrs-full:BasicEarningsLossPerShare",
    ],
    "operating_cashflow": [
        "ifrs-full:CashFlowsFromUsedInOperatingActivities",
    ],
    "capex": [
        "ifrs-full:PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "ifrs-full:AcquisitionOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities",
        "ifrs-full:PurchaseOfPropertyPlantAndEquipment",
    ],
    "interest_expense": [
        "ifrs-full:FinanceCosts",
        "ifrs-full:InterestExpense",
    ],
    "income_tax_expense": [
        "ifrs-full:IncomeTaxExpenseContinuingOperations",
        "ifrs-full:IncomeTaxExpense",
    ],
    "depreciation_amortization": [
        "ifrs-full:DepreciationAndAmortisationExpense",
        "ifrs-full:DepreciationAmortisationAndImpairmentLossReversalOfImpairmentLossRecognisedInProfitOrLoss",
        "ifrs-full:AdjustmentsForDepreciationAndAmortisationExpense",
    ],
    "shares_outstanding": [
        "ifrs-full:WeightedAverageNumberOfSharesOutstandingBasic",
        "ifrs-full:WeightedAverageNumberOfOrdinarySharesOutstanding",
    ],
}

INSTANT_CONCEPTS: dict[str, list[str]] = {
    "total_assets": [
        "ifrs-full:Assets",
        "ifrs-full:EquityAndLiabilities",
    ],
    "total_liabilities": [
        "ifrs-full:Liabilities",
    ],
    "equity": [
        "ifrs-full:EquityAttributableToOwnersOfParent",
        "ifrs-full:Equity",
    ],
    "current_assets": [
        "ifrs-full:CurrentAssets",
    ],
    "current_liabilities": [
        "ifrs-full:CurrentLiabilities",
    ],
    "inventory": [
        "ifrs-full:Inventories",
    ],
    "prepaid_expenses": [
        "ifrs-full:Prepayments",
        "ifrs-full:OtherCurrentAssets",
    ],
    "cash_and_equivalents": [
        "ifrs-full:CashAndCashEquivalents",
        "ifrs-full:CashAndCashEquivalentsIfDifferentFromStatementOfFinancialPosition",
    ],
    "long_term_debt": [
        "ifrs-full:LongtermBorrowings",
        "ifrs-full:NoncurrentPortionOfLongtermBorrowings",
        "ifrs-full:NoncurrentBorrowings",
    ],
    "shares_outstanding_instant": [
        # Fallback for shares when weighted average is missing
        "ifrs-full:NumberOfSharesOutstanding",
        "ifrs-full:NumberOfSharesIssued",
    ],
}


def _period_end_str(filing_end: date) -> str:
    """Convert filing last_end_date to ESEF instant/duration-end string."""
    nxt = filing_end + timedelta(days=1)
    return nxt.strftime("%Y-%m-%dT00:00:00")


def _parse_value(raw: str | None, is_share_count: bool = False) -> float | None:
    """Parse a string numeric value to float, return None on failure."""
    if raw is None:
        return None
    try:
        val = float(raw)
        return val if val != 0.0 else None
    except (ValueError, TypeError):
        return None


def _build_concept_index(
    facts: dict[str, Any],
    period_end: str,
) -> tuple[dict[str, float | None], dict[str, float | None]]:
    """
    Build two lookup dicts from the facts blob:
      duration_vals: concept → value for the period ending on period_end
      instant_vals:  concept → value at the instant period_end
    Only plain facts (no extra dimensions beyond concept/entity/period/unit/language).
    """
    duration_vals: dict[str, float | None] = {}
    instant_vals: dict[str, float | None] = {}

    for fact in facts.values():
        dims = fact.get("dimensions", {})
        concept = dims.get("concept", "")
        period = dims.get("period", "")
        raw = fact.get("value")

        # Skip dimensioned facts (segment, geographical, etc.)
        extra = {k for k in dims if k not in {"concept", "entity", "period", "unit", "language"}}
        if extra:
            continue

        val = _parse_value(raw)
        if val is None:
            continue

        # Duration: period string "START/END" where END == period_end
        if "/" in period:
            end_part = period.split("/")[1]
            if end_part == period_end:
                # Keep the largest magnitude value when duplicates exist
                if concept not in duration_vals or abs(val) > abs(duration_vals.get(concept) or 0):
                    duration_vals[concept] = val
        else:
            # Instant: period string IS period_end
            if period == period_end:
                if concept not in instant_vals or abs(val) > abs(instant_vals.get(concept) or 0):
                    instant_vals[concept] = val

    return duration_vals, instant_vals


def _pick(lookup: dict[str, float | None], candidates: list[str]) -> float | None:
    """Return first non-None value from lookup for the given candidates."""
    for c in candidates:
        v = lookup.get(c)
        if v is not None:
            return v
    return None


def extract_summary(facts: dict[str, Any], filing_end_date: date) -> dict[str, float | None]:
    """
    Extract the 21-field financial summary from a filings.xbrl.org JSON facts blob.

    Args:
        facts: The 'facts' dict from the xBRL-JSON response.
        filing_end_date: The filing's last_end_date (e.g. date(2022, 12, 31)).

    Returns:
        Dict mapping our canonical field names to float values or None.
    """
    period_end = _period_end_str(filing_end_date)
    dur, inst = _build_concept_index(facts, period_end)

    shares = _pick(dur, DURATION_CONCEPTS["shares_outstanding"])
    if shares is None:
        shares = _pick(inst, INSTANT_CONCEPTS["shares_outstanding_instant"])

    capex_raw = _pick(dur, DURATION_CONCEPTS["capex"])
    # CapEx is reported as positive in IFRS investing activities (outflow)
    # We store it as negative to match us-gaap convention (net outflow)
    capex = -abs(capex_raw) if capex_raw is not None else None

    op_cf = _pick(dur, DURATION_CONCEPTS["operating_cashflow"])
    free_cashflow = (op_cf + capex) if (op_cf is not None and capex is not None) else None

    return {
        "revenue":                 _pick(dur, DURATION_CONCEPTS["revenue"]),
        "gross_profit":            _pick(dur, DURATION_CONCEPTS["gross_profit"]),
        "operating_income":        _pick(dur, DURATION_CONCEPTS["operating_income"]),
        "net_income":              _pick(dur, DURATION_CONCEPTS["net_income"]),
        "eps":                     _pick(dur, DURATION_CONCEPTS["eps"]),
        "operating_cashflow":      op_cf,
        "capex":                   capex,
        "free_cashflow":           free_cashflow,
        "interest_expense":        _pick(dur, DURATION_CONCEPTS["interest_expense"]),
        "income_tax_expense":      _pick(dur, DURATION_CONCEPTS["income_tax_expense"]),
        "depreciation_amortization": _pick(dur, DURATION_CONCEPTS["depreciation_amortization"]),
        "shares_outstanding":      shares,
        "total_assets":            _pick(inst, INSTANT_CONCEPTS["total_assets"]),
        "total_liabilities":       _pick(inst, INSTANT_CONCEPTS["total_liabilities"]),
        "equity":                  _pick(inst, INSTANT_CONCEPTS["equity"]),
        "current_assets":          _pick(inst, INSTANT_CONCEPTS["current_assets"]),
        "current_liabilities":     _pick(inst, INSTANT_CONCEPTS["current_liabilities"]),
        "inventory":               _pick(inst, INSTANT_CONCEPTS["inventory"]),
        "prepaid_expenses":        _pick(inst, INSTANT_CONCEPTS["prepaid_expenses"]),
        "cash_and_equivalents":    _pick(inst, INSTANT_CONCEPTS["cash_and_equivalents"]),
        "long_term_debt":          _pick(inst, INSTANT_CONCEPTS["long_term_debt"]),
    }
