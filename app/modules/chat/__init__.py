"""Chat module (FR-10, FR-11).

Owns tables: channel_messages, conversations, conversation_participants,
direct_messages, user_blocks.
Public interface: ``app.modules.chat.service``.
May call the services of: accounts (route guards, sender and participant
names), channels (visibility and membership checks), materials, questions and
quizzes (preview cards for shared resources, S11.2), moderation (reporting a direct
message).
"""
