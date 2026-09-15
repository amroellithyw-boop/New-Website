"""Client profiles: the single source of personalisation."""

from .knowledge import ClientKnowledge, Fact, OpenQuestion, Precedent
from .profile import SERVICES, ClientPolicy, ClientProfile, ServicePackage
from .tax_regions import ONTARIO_CCPC, SALES_TAX, CorporateRates, SalesTaxRegime, sales_tax_for

__all__ = [
    "ClientProfile", "ClientPolicy", "ClientKnowledge", "Fact", "Precedent", "OpenQuestion", "ServicePackage", "SERVICES",
    "SalesTaxRegime", "SALES_TAX", "sales_tax_for", "CorporateRates", "ONTARIO_CCPC",
]
