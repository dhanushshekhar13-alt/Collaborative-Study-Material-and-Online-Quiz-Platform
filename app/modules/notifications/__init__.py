"""Notifications module (FR-14).

Owns tables: notifications, notification_preferences.
Public interface: ``app.modules.notifications.service``.
May call the services of: accounts (route guards, sender names). It reacts to
chat, channels and quizzes through events (``app.core.events``), never importing them.
"""
