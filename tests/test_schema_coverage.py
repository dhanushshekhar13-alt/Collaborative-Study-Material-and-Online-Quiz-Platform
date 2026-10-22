"""S00.2: the schema covers every entity referenced by FR-01 to FR-14."""

import app.models  # noqa: F401
from app.core.database import Base

ENTITIES_BY_REQUIREMENT = {
    "FR-01 accounts, profiles, contribution history": {
        "users",
        "profile_pictures",
        "reputation_events",
    },
    "FR-02 channels and membership": {"channels", "channel_memberships"},
    "FR-03 authorized vs normal channel content": {"channels", "study_materials", "quizzes"},
    "FR-04 materials, ratings, reviews, reports": {
        "study_materials",
        "material_files",
        "material_reviews",
        "reports",
    },
    "FR-05 question bank and quizzes": {
        "questions",
        "question_ratings",
        "quizzes",
        "quiz_questions",
    },
    "FR-06 attempts and results": {"quiz_attempts", "attempt_answers"},
    "FR-07 LLM generation and adaptive quizzes": {
        "llm_requests",
        "adaptive_sessions",
        "questions",
        "attempt_answers",
    },
    "FR-08 descriptive answer evaluation": {"attempt_answers", "llm_requests"},
    "FR-09 LLM explanations and channel-scoped help": {"llm_requests"},
    "FR-10 channel chat with threads": {"channel_messages"},
    "FR-11 direct messages, blocking, sharing": {
        "conversations",
        "conversation_participants",
        "direct_messages",
        "user_blocks",
    },
    "FR-12 performance analytics": {"quiz_attempts", "attempt_answers"},
    "FR-13 reputation, badges, reports": {"reputation_events", "badges", "user_badges", "reports"},
    "FR-14 notifications and admin actions": {
        "notifications",
        "notification_preferences",
        "moderation_actions",
    },
}


def test_every_requirement_has_its_tables():
    tables = set(Base.metadata.tables)
    missing = {fr: needed - tables for fr, needed in ENTITIES_BY_REQUIREMENT.items()}
    assert {fr: m for fr, m in missing.items() if m} == {}


def test_every_table_traces_to_a_requirement():
    traced = set().union(*ENTITIES_BY_REQUIREMENT.values())
    assert set(Base.metadata.tables) - traced == set()


def test_threaded_replies_and_shared_resources_are_modelled():
    messages = Base.metadata.tables["channel_messages"].c
    assert "parent_id" in messages and "shared_resource_id" in messages
    assert "expected_concepts" in Base.metadata.tables["questions"].c
