"""Analytics module (FR-12).

Owns tables: performance_attempts, performance_topics (stored figures derived from
quiz attempts, always rebuildable from them), recommendation_opens (S12.2).
Public interface: ``app.modules.analytics.service``.
May call the services of: ``accounts`` (route guards), ``quizzes``, and for
recommendations (S12.2) ``channels`` (the user's scope), ``materials``, ``questions``.
"""
