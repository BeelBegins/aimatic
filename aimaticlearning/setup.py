from .lms_learning.setup import create_roles


def after_install():
	create_roles()

