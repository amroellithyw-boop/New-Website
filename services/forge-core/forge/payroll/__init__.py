"""Canadian payroll: statutory deductions computed exactly, with annual caps."""

from .canada import PayrollInput, PayrollResult, annual_tax, calculate_pay
from .rates import PAY_PERIODS, PayrollRates, rates_for

__all__ = [
    "PayrollInput", "PayrollResult", "calculate_pay", "annual_tax",
    "PayrollRates", "rates_for", "PAY_PERIODS",
]
