"""Questions module (FR-05, FR-07).

Owns tables: questions, question_ratings.
Public interface: ``app.modules.questions.service``.
May call the services of: accounts (route guards), channels (bank scope and
membership), moderation (question reports), reputation (peer-rating reputation, S13.1).
"""
