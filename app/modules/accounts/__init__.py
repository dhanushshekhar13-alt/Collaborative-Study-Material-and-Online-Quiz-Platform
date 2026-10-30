"""Accounts module (FR-01).

Owns tables: users, profile_pictures.
Public interface: ``app.modules.accounts.service`` (account functions, plus the
``current_user`` / ``require_capability`` / ``require_roles`` route guards).
May call the services of: none.
"""
