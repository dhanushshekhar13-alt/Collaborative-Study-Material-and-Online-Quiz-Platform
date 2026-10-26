"""Analytics module (FR-12).

Owns tables: performance_attempts, performance_topics (stored figures derived from
quiz attempts, always rebuildable from them).
Public interface: ``app.modules.analytics.service``.
May call the services of: ``accounts`` (route guards), ``quizzes``.
"""
