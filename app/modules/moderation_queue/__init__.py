"""Moderation queue module (FR-13, story S13.2): the report queue and moderator actions.

Owns tables: none (reports and the action log belong to moderation; this module
assembles the queue from the services of the modules that own the content).
Public interface: ``app.modules.moderation_queue.service``.
May call the services of: accounts, channels, llm (reported AI responses, NFR-06),
materials, moderation, questions.
"""
