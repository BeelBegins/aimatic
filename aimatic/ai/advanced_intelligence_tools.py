"""Aggregate exports for focused certified intelligence modules."""

from aimatic.ai.anomaly_detection import TOOL_DISPATCH as _ANOMALY_DISPATCH
from aimatic.ai.anomaly_detection import TOOL_SPECS as _ANOMALY_SPECS
from aimatic.ai.basket_analysis import TOOL_DISPATCH as _BASKET_DISPATCH
from aimatic.ai.basket_analysis import TOOL_SPECS as _BASKET_SPECS
from aimatic.ai.customer_intelligence import TOOL_DISPATCH as _CUSTOMER_DISPATCH
from aimatic.ai.customer_intelligence import TOOL_SPECS as _CUSTOMER_SPECS
from aimatic.ai.inventory_optimization import TOOL_DISPATCH as _TRANSFER_DISPATCH
from aimatic.ai.inventory_optimization import TOOL_SPECS as _TRANSFER_SPECS
from aimatic.ai.promotion_analysis import TOOL_DISPATCH as _PROMOTION_DISPATCH
from aimatic.ai.promotion_analysis import TOOL_SPECS as _PROMOTION_SPECS
from aimatic.ai.vendor_intelligence import TOOL_DISPATCH as _VENDOR_DISPATCH
from aimatic.ai.vendor_intelligence import TOOL_SPECS as _VENDOR_SPECS

TOOL_SPECS = (
	_TRANSFER_SPECS + _PROMOTION_SPECS + _CUSTOMER_SPECS + _BASKET_SPECS + _VENDOR_SPECS + _ANOMALY_SPECS
)
TOOL_DISPATCH = {
	**_TRANSFER_DISPATCH,
	**_PROMOTION_DISPATCH,
	**_CUSTOMER_DISPATCH,
	**_BASKET_DISPATCH,
	**_VENDOR_DISPATCH,
	**_ANOMALY_DISPATCH,
}
