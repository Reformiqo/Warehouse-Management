"""Seed the one-off initial count on sites that already had the app. A fresh
install skips patches entirely and seeds through after_install instead, so the
work itself lives in setup/initial_reconciliation.py.
"""

from warehouse_management.setup.initial_reconciliation import create_assignments


def execute():
	create_assignments()
