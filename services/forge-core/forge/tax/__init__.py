"""Tax readiness: return preparation, the compliance calendar, provisions and slips.

Nothing here files anything. Every output is a working paper for a human who
signs, which is what the constitution requires for regulated work.
"""

from .calendar import Deadline, hst_filing_frequency, tax_calendar
from .corporate import TaxProvision, corporate_tax_provision
from .hst import SalesTaxReturn, sales_tax_return
from .slips import SlipObligation, slip_obligations

__all__ = ["SalesTaxReturn", "sales_tax_return", "Deadline", "tax_calendar", "hst_filing_frequency",
           "TaxProvision", "corporate_tax_provision", "SlipObligation", "slip_obligations"]
