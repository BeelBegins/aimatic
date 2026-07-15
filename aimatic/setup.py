from aimatic.label_printing.setup import after_install as setup_label_printing
from aimatic.restaurant.setup import create_roles as create_restaurant_roles


def after_install():
	setup_label_printing()
	create_restaurant_roles()
