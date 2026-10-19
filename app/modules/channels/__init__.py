"""Channels module (FR-02, FR-03).

Owns tables: channels, channel_memberships.
Public interface: ``app.modules.channels.service``.
May call the services of: accounts (route guards and permissions).
HTTP routes: ``app.modules.channels.router`` at ``/api/v1/channels``.
"""
