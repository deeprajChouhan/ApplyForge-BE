"""
Market configuration layer (Recruiter OS, Phase 1).

Compensation, notice-period, and employment-type presentation is country-specific.
We keep it as code, not a DB table: it's static reference data and adding a new
country is a one-file change. The rest of the module never branches on country
codes directly — it asks `market_config.get_config(country_code)`.

Currency and salary amounts are always stored numerically (annual, in the
currency's minor unit's parent — e.g. INR annual, not lakhs). Display helpers
convert to the market's preferred format (e.g. "₹28 LPA", "AED 22,000 / month",
"£95,000 / year").
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass(frozen=True)
class MarketConfig:
    country_code: str
    country_name: str
    default_currency: str
    default_salary_period: str          # "YEAR" | "MONTH" | "DAY" | "HOUR"
    salary_display_format: str          # "annual" | "monthly" | "lpa"
    notice_period_presets: list[dict[str, Any]] = field(default_factory=list)
    employment_types: list[str] = field(default_factory=lambda: [
        "full_time", "part_time", "contract", "internship", "temporary",
    ])
    compensation_schema: list[str] = field(default_factory=lambda: [
        "amount", "currency", "period", "fixed", "variable", "bonus",
    ])
    contract_schema: list[str] = field(default_factory=lambda: [
        "rate", "rate_period", "currency", "contract_classification",
    ])
    date_format: str = "YYYY-MM-DD"
    phone_format: str = "international"
    terminology: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_UK = MarketConfig(
    country_code="GB",
    country_name="United Kingdom",
    default_currency="GBP",
    default_salary_period="YEAR",
    salary_display_format="annual",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "1 week", "value": 1, "unit": "WEEK"},
        {"label": "2 weeks", "value": 2, "unit": "WEEK"},
        {"label": "1 month", "value": 1, "unit": "MONTH"},
        {"label": "2 months", "value": 2, "unit": "MONTH"},
        {"label": "3 months", "value": 3, "unit": "MONTH"},
    ],
    compensation_schema=["amount", "currency", "period", "bonus"],
    contract_schema=["rate", "rate_period", "currency", "contract_classification"],
    terminology={"salary": "salary", "compensation_label": "Salary", "ir35": True},
)

_INDIA = MarketConfig(
    country_code="IN",
    country_name="India",
    default_currency="INR",
    default_salary_period="YEAR",
    salary_display_format="lpa",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "30 days", "value": 30, "unit": "DAY"},
        {"label": "60 days", "value": 60, "unit": "DAY"},
        {"label": "90 days", "value": 90, "unit": "DAY"},
        {"label": "3 months (LWD negotiable)", "value": 3, "unit": "MONTH"},
    ],
    compensation_schema=["amount", "currency", "period", "fixed", "variable"],
    terminology={"salary": "CTC", "compensation_label": "CTC", "fixed_label": "Fixed", "variable_label": "Variable"},
)

_UAE = MarketConfig(
    country_code="AE",
    country_name="United Arab Emirates",
    default_currency="AED",
    default_salary_period="MONTH",
    salary_display_format="monthly",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "30 days", "value": 30, "unit": "DAY"},
        {"label": "60 days", "value": 60, "unit": "DAY"},
        {"label": "90 days", "value": 90, "unit": "DAY"},
    ],
    compensation_schema=["amount", "currency", "period", "basic_salary", "allowances", "housing_allowance", "transport_allowance"],
    terminology={"salary": "salary", "compensation_label": "Salary", "basic_label": "Basic", "allowances_label": "Allowances"},
)

_US = MarketConfig(
    country_code="US",
    country_name="United States",
    default_currency="USD",
    default_salary_period="YEAR",
    salary_display_format="annual",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "2 weeks", "value": 2, "unit": "WEEK"},
        {"label": "4 weeks", "value": 4, "unit": "WEEK"},
        {"label": "1 month", "value": 1, "unit": "MONTH"},
    ],
    compensation_schema=["amount", "currency", "period", "bonus", "equity"],
    terminology={"salary": "salary", "compensation_label": "Salary"},
)

_CANADA = MarketConfig(
    country_code="CA",
    country_name="Canada",
    default_currency="CAD",
    default_salary_period="YEAR",
    salary_display_format="annual",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "2 weeks", "value": 2, "unit": "WEEK"},
        {"label": "1 month", "value": 1, "unit": "MONTH"},
    ],
    compensation_schema=["amount", "currency", "period", "bonus"],
    terminology={"salary": "salary", "compensation_label": "Salary"},
)

_AUSTRALIA = MarketConfig(
    country_code="AU",
    country_name="Australia",
    default_currency="AUD",
    default_salary_period="YEAR",
    salary_display_format="annual",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "2 weeks", "value": 2, "unit": "WEEK"},
        {"label": "4 weeks", "value": 4, "unit": "WEEK"},
    ],
    compensation_schema=["amount", "currency", "period", "superannuation"],
    terminology={"salary": "salary", "compensation_label": "Package"},
)

_SINGAPORE = MarketConfig(
    country_code="SG",
    country_name="Singapore",
    default_currency="SGD",
    default_salary_period="MONTH",
    salary_display_format="monthly",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "1 month", "value": 1, "unit": "MONTH"},
        {"label": "2 months", "value": 2, "unit": "MONTH"},
        {"label": "3 months", "value": 3, "unit": "MONTH"},
    ],
    compensation_schema=["amount", "currency", "period", "bonus", "aws"],
    terminology={"salary": "salary", "compensation_label": "Salary"},
)

_GERMANY = MarketConfig(
    country_code="DE",
    country_name="Germany",
    default_currency="EUR",
    default_salary_period="YEAR",
    salary_display_format="annual",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "1 month", "value": 1, "unit": "MONTH"},
        {"label": "3 months", "value": 3, "unit": "MONTH"},
        {"label": "6 months", "value": 6, "unit": "MONTH"},
    ],
    compensation_schema=["amount", "currency", "period", "bonus"],
    terminology={"salary": "gross_salary", "compensation_label": "Gross Salary"},
)

# Neutral fallback used when a role's country_code is missing or unrecognised.
_DEFAULT = MarketConfig(
    country_code="",
    country_name="",
    default_currency="USD",
    default_salary_period="YEAR",
    salary_display_format="annual",
    notice_period_presets=[
        {"label": "Immediate", "value": 0, "unit": "DAY"},
        {"label": "2 weeks", "value": 2, "unit": "WEEK"},
        {"label": "1 month", "value": 1, "unit": "MONTH"},
    ],
)

_REGISTRY: dict[str, MarketConfig] = {
    "GB": _UK,
    "IN": _INDIA,
    "AE": _UAE,
    "US": _US,
    "CA": _CANADA,
    "AU": _AUSTRALIA,
    "SG": _SINGAPORE,
    "DE": _GERMANY,
}


def all_configs() -> list[MarketConfig]:
    return list(_REGISTRY.values())


def get_config(country_code: str | None) -> MarketConfig:
    if not country_code:
        return _DEFAULT
    return _REGISTRY.get(country_code.upper(), _DEFAULT)


def is_supported(country_code: str | None) -> bool:
    return bool(country_code) and country_code.upper() in _REGISTRY


def format_compensation(comp: dict[str, Any] | None, country_code: str | None = None) -> str:
    """
    Human-readable, market-aware string. Never raises — falls back to a
    numeric-with-currency representation when the shape is unexpected.
    """
    if not comp or not isinstance(comp, dict):
        return ""
    cfg = get_config(country_code)
    amount = comp.get("amount")
    currency = comp.get("currency") or cfg.default_currency
    period = (comp.get("period") or cfg.default_salary_period).upper()
    if amount is None:
        return ""

    if cfg.salary_display_format == "lpa" and currency == "INR" and period == "YEAR":
        lpa = amount / 100000.0
        # 28.0 → "₹28 LPA"; 28.5 → "₹28.5 LPA"
        s = f"{lpa:.1f}".rstrip("0").rstrip(".")
        return f"₹{s} LPA"

    if cfg.salary_display_format == "monthly" or period == "MONTH":
        return f"{currency} {amount:,.0f} / month"

    # annual default
    symbol = {"GBP": "£", "USD": "$", "EUR": "€", "INR": "₹", "AUD": "A$", "CAD": "C$"}.get(currency, "")
    if symbol:
        return f"{symbol}{amount:,.0f}"
    return f"{currency} {amount:,.0f}"
