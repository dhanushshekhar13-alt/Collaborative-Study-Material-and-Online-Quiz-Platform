"""Admin module (FR-14, story S14.2).

Owns tables: none. Admin management is assembled from other modules' public services:
accounts (suspension), channels (pending requests), materials and questions (content
removal), moderation (the review queue and the audit log in ``moderation_actions``)
and notifications (telling a poster why their content was removed).
Public interface: ``app.modules.admin.service``.
May call the services of: ``accounts``, ``channels``, ``materials``, ``moderation``,
``notifications``, ``questions``.
"""
