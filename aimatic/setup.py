from aimatic.label_printing.setup import after_install as setup_label_printing
from aimatic.patches.create_pos_supervisor_role import execute as create_pos_supervisor_role
from aimatic.patches.create_pos_user_role import execute as create_pos_user_role
from aimatic.patches.repair_item_custom_docperms import execute as repair_item_custom_docperms
from aimatic.restaurant.setup import create_roles as create_restaurant_roles


def after_install():
	setup_label_printing()
	create_restaurant_roles()
	setup_pos_master_data_permissions()


def setup_pos_master_data_permissions():
	"""Create the POS roles and their Item grants on fresh installs.

	Frappe marks patches completed instead of executing them when an app is
	first installed, so the equivalent migration patches alone cannot prepare
	a brand-new site. Every operation here is idempotent and remains safe when
	the test bootstrap calls it again.
	"""
	create_pos_user_role()
	create_pos_supervisor_role()
	repair_item_custom_docperms()
