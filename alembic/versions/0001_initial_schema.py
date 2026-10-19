"""Initial schema: every entity referenced by FR-01 to FR-14 (S00.2).

Revision ID: 0001
Revises: 
Create Date: 2026-10-19 13:06:02.919290
"""
from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa


revision: str = '0001'
down_revision: Union[str, None] = None
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('badges',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('code', sa.String(length=50), nullable=False),
    sa.Column('name', sa.String(length=100), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('code')
    )
    op.create_table('conversations',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    op.create_table('users',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('email', sa.String(length=255), nullable=False),
    sa.Column('display_name', sa.String(length=100), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('student', 'authorized_user', 'admin', name='user_role', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('bio', sa.Text(), nullable=True),
    sa.Column('institution', sa.String(length=150), nullable=True),
    sa.Column('reputation', sa.Integer(), server_default='0', nullable=False),
    sa.Column('is_suspended', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_users_email'), ['email'], unique=True)

    op.create_table('channels',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('subject', sa.String(length=100), nullable=True),
    sa.Column('topic', sa.String(length=100), nullable=True),
    sa.Column('channel_type', sa.Enum('normal', 'authorized', name='channel_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('approval_status', sa.Enum('pending', 'approved', 'rejected', name='channel_approval_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('created_by', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['created_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_channels_created_by'), ['created_by'], unique=False)
        batch_op.create_index(batch_op.f('ix_channels_name'), ['name'], unique=False)
        batch_op.create_index(batch_op.f('ix_channels_subject'), ['subject'], unique=False)

    op.create_table('conversation_participants',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('conversation_id', 'user_id', name='uq_conversation_participant')
    )
    with op.batch_alter_table('conversation_participants', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_conversation_participants_user_id'), ['user_id'], unique=False)

    op.create_table('direct_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('conversation_id', sa.Integer(), nullable=False),
    sa.Column('sender_id', sa.Integer(), nullable=False),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('shared_resource_type', sa.Enum('material', 'question', 'quiz', name='direct_message_resource', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('shared_resource_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['conversation_id'], ['conversations.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('direct_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_direct_messages_conversation_id'), ['conversation_id'], unique=False)

    op.create_table('notification_preferences',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('channel_activity', 'reply', 'mention', 'direct_message', 'quiz_update', 'moderation', name='notification_preference_kind', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('enabled', sa.Boolean(), server_default='1', nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'kind', name='uq_notification_preference')
    )
    op.create_table('notifications',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('kind', sa.Enum('channel_activity', 'reply', 'mention', 'direct_message', 'quiz_update', 'moderation', name='notification_kind', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('body', sa.Text(), nullable=True),
    sa.Column('link', sa.String(length=500), nullable=True),
    sa.Column('is_read', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_notifications_user_id'), ['user_id'], unique=False)

    op.create_table('reputation_events',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('points', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=64), nullable=False),
    sa.Column('source_type', sa.String(length=32), nullable=True),
    sa.Column('source_id', sa.Integer(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('reputation_events', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reputation_events_user_id'), ['user_id'], unique=False)

    op.create_table('user_badges',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('badge_id', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['badge_id'], ['badges.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('user_id', 'badge_id', name='uq_user_badge')
    )
    op.create_table('user_blocks',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('blocker_id', sa.Integer(), nullable=False),
    sa.Column('blocked_id', sa.Integer(), nullable=False),
    sa.Column('reason', sa.String(length=255), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('blocker_id <> blocked_id', name='ck_user_block_not_self'),
    sa.ForeignKeyConstraint(['blocked_id'], ['users.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['blocker_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('blocker_id', 'blocked_id', name='uq_user_block')
    )
    op.create_table('channel_memberships',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('role', sa.Enum('member', 'moderator', 'owner', name='channel_member_role', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('joined_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('channel_id', 'user_id', name='uq_channel_member')
    )
    with op.batch_alter_table('channel_memberships', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_channel_memberships_user_id'), ['user_id'], unique=False)

    op.create_table('channel_messages',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=False),
    sa.Column('sender_id', sa.Integer(), nullable=False),
    sa.Column('parent_id', sa.Integer(), nullable=True),
    sa.Column('body', sa.Text(), nullable=False),
    sa.Column('shared_resource_type', sa.Enum('material', 'question', 'quiz', name='channel_message_resource', native_enum=False, create_constraint=True, length=32), nullable=True),
    sa.Column('shared_resource_id', sa.Integer(), nullable=True),
    sa.Column('is_deleted', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['parent_id'], ['channel_messages.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['sender_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('channel_messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_channel_messages_channel_id'), ['channel_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_channel_messages_parent_id'), ['parent_id'], unique=False)

    op.create_table('llm_requests',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=True),
    sa.Column('task', sa.Enum('generate_questions', 'adapt_difficulty', 'evaluate_answer', 'explain', 'generate_quiz', name='llm_task', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('subject', sa.String(length=100), nullable=True),
    sa.Column('topic', sa.String(length=100), nullable=True),
    sa.Column('prompt', sa.Text(), nullable=False),
    sa.Column('status', sa.Enum('pending', 'succeeded', 'failed', name='llm_request_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('output', sa.JSON(), nullable=True),
    sa.Column('error', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('llm_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_llm_requests_user_id'), ['user_id'], unique=False)

    op.create_table('questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=True),
    sa.Column('author_id', sa.Integer(), nullable=False),
    sa.Column('subject', sa.String(length=100), nullable=False),
    sa.Column('topic', sa.String(length=100), nullable=False),
    sa.Column('difficulty', sa.Enum('easy', 'medium', 'hard', name='question_difficulty', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('question_type', sa.Enum('multiple_choice', 'true_false', 'short_answer', 'descriptive', name='question_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('text', sa.Text(), nullable=False),
    sa.Column('options', sa.JSON(), nullable=True),
    sa.Column('correct_answer', sa.Text(), nullable=True),
    sa.Column('explanation', sa.Text(), nullable=True),
    sa.Column('expected_concepts', sa.JSON(), nullable=True),
    sa.Column('source', sa.Enum('peer', 'llm', name='question_source', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('status', sa.Enum('draft', 'published', 'removed', name='question_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('extra', sa.JSON(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['author_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_questions_author_id'), ['author_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_questions_channel_id'), ['channel_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_questions_subject'), ['subject'], unique=False)
        batch_op.create_index(batch_op.f('ix_questions_topic'), ['topic'], unique=False)

    op.create_table('quizzes',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=True),
    sa.Column('creator_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('subject', sa.String(length=100), nullable=False),
    sa.Column('topic', sa.String(length=100), nullable=False),
    sa.Column('difficulty', sa.String(length=16), nullable=False),
    sa.Column('duration_minutes', sa.Integer(), nullable=False),
    sa.Column('is_adaptive', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('status', sa.Enum('draft', 'published', 'removed', name='quiz_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['creator_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quizzes_channel_id'), ['channel_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quizzes_creator_id'), ['creator_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quizzes_subject'), ['subject'], unique=False)
        batch_op.create_index(batch_op.f('ix_quizzes_topic'), ['topic'], unique=False)

    op.create_table('reports',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('reporter_id', sa.Integer(), nullable=False),
    sa.Column('target_type', sa.Enum('material', 'question', 'quiz', 'channel_message', 'direct_message', 'user', 'channel', name='report_target', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=True),
    sa.Column('reason', sa.Enum('incorrect', 'inappropriate', 'duplicate', 'misleading', 'spam', 'other', name='report_reason', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('details', sa.Text(), nullable=True),
    sa.Column('status', sa.Enum('open', 'resolved', 'dismissed', name='report_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('resolved_by', sa.Integer(), nullable=True),
    sa.Column('resolution_note', sa.Text(), nullable=True),
    sa.Column('resolved_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['reporter_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['resolved_by'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_reports_reporter_id'), ['reporter_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_reports_status'), ['status'], unique=False)

    op.create_table('study_materials',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('channel_id', sa.Integer(), nullable=True),
    sa.Column('uploader_id', sa.Integer(), nullable=False),
    sa.Column('title', sa.String(length=200), nullable=False),
    sa.Column('description', sa.Text(), nullable=True),
    sa.Column('material_type', sa.Enum('notes', 'pdf', 'presentation', 'link', 'other', name='material_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('subject', sa.String(length=100), nullable=False),
    sa.Column('topic', sa.String(length=100), nullable=True),
    sa.Column('semester', sa.String(length=20), nullable=True),
    sa.Column('file_path', sa.String(length=500), nullable=True),
    sa.Column('url', sa.String(length=1000), nullable=True),
    sa.Column('is_official', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('status', sa.Enum('active', 'removed', name='material_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['channel_id'], ['channels.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['uploader_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('study_materials', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_study_materials_channel_id'), ['channel_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_study_materials_subject'), ['subject'], unique=False)
        batch_op.create_index(batch_op.f('ix_study_materials_topic'), ['topic'], unique=False)
        batch_op.create_index(batch_op.f('ix_study_materials_uploader_id'), ['uploader_id'], unique=False)

    op.create_table('material_reviews',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('material_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('review', sa.Text(), nullable=True),
    sa.Column('is_helpful', sa.Boolean(), server_default='0', nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_material_review_rating'),
    sa.ForeignKeyConstraint(['material_id'], ['study_materials.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('material_id', 'user_id', name='uq_material_review')
    )
    op.create_table('moderation_actions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('actor_id', sa.Integer(), nullable=False),
    sa.Column('action', sa.Enum('remove_content', 'restore_content', 'suspend_user', 'unsuspend_user', 'approve_channel', 'reject_channel', 'warn_user', name='moderation_action_type', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('target_type', sa.Enum('material', 'question', 'quiz', 'channel_message', 'direct_message', 'user', 'channel', name='moderation_action_target', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('target_id', sa.Integer(), nullable=False),
    sa.Column('report_id', sa.Integer(), nullable=True),
    sa.Column('note', sa.Text(), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.ForeignKeyConstraint(['actor_id'], ['users.id'], ),
    sa.ForeignKeyConstraint(['report_id'], ['reports.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('moderation_actions', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_moderation_actions_actor_id'), ['actor_id'], unique=False)

    op.create_table('question_ratings',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('rating', sa.Integer(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.CheckConstraint('rating BETWEEN 1 AND 5', name='ck_question_rating_value'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('question_id', 'user_id', name='uq_question_rating')
    )
    op.create_table('quiz_attempts',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('quiz_id', sa.Integer(), nullable=False),
    sa.Column('user_id', sa.Integer(), nullable=False),
    sa.Column('status', sa.Enum('in_progress', 'submitted', name='attempt_status', native_enum=False, create_constraint=True, length=32), nullable=False),
    sa.Column('started_at', sa.DateTime(timezone=True), server_default=sa.text('(CURRENT_TIMESTAMP)'), nullable=False),
    sa.Column('submitted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('max_score', sa.Float(), nullable=True),
    sa.ForeignKeyConstraint(['quiz_id'], ['quizzes.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['user_id'], ['users.id'], ),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('quiz_attempts', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_quiz_attempts_quiz_id'), ['quiz_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_quiz_attempts_user_id'), ['user_id'], unique=False)

    op.create_table('quiz_questions',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('quiz_id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('position', sa.Integer(), nullable=False),
    sa.Column('points', sa.Float(), server_default='1', nullable=False),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ),
    sa.ForeignKeyConstraint(['quiz_id'], ['quizzes.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('quiz_id', 'question_id', name='uq_quiz_question')
    )
    op.create_table('attempt_answers',
    sa.Column('id', sa.Integer(), nullable=False),
    sa.Column('attempt_id', sa.Integer(), nullable=False),
    sa.Column('question_id', sa.Integer(), nullable=False),
    sa.Column('response', sa.Text(), nullable=True),
    sa.Column('is_correct', sa.Boolean(), nullable=True),
    sa.Column('score', sa.Float(), nullable=True),
    sa.Column('difficulty_served', sa.String(length=16), nullable=True),
    sa.Column('feedback', sa.JSON(), nullable=True),
    sa.ForeignKeyConstraint(['attempt_id'], ['quiz_attempts.id'], ondelete='CASCADE'),
    sa.ForeignKeyConstraint(['question_id'], ['questions.id'], ),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('attempt_id', 'question_id', name='uq_attempt_answer')
    )


def downgrade() -> None:
    op.drop_table('attempt_answers')
    op.drop_table('quiz_questions')
    with op.batch_alter_table('quiz_attempts', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quiz_attempts_user_id'))
        batch_op.drop_index(batch_op.f('ix_quiz_attempts_quiz_id'))

    op.drop_table('quiz_attempts')
    op.drop_table('question_ratings')
    with op.batch_alter_table('moderation_actions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_moderation_actions_actor_id'))

    op.drop_table('moderation_actions')
    op.drop_table('material_reviews')
    with op.batch_alter_table('study_materials', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_study_materials_uploader_id'))
        batch_op.drop_index(batch_op.f('ix_study_materials_topic'))
        batch_op.drop_index(batch_op.f('ix_study_materials_subject'))
        batch_op.drop_index(batch_op.f('ix_study_materials_channel_id'))

    op.drop_table('study_materials')
    with op.batch_alter_table('reports', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reports_status'))
        batch_op.drop_index(batch_op.f('ix_reports_reporter_id'))

    op.drop_table('reports')
    with op.batch_alter_table('quizzes', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_quizzes_topic'))
        batch_op.drop_index(batch_op.f('ix_quizzes_subject'))
        batch_op.drop_index(batch_op.f('ix_quizzes_creator_id'))
        batch_op.drop_index(batch_op.f('ix_quizzes_channel_id'))

    op.drop_table('quizzes')
    with op.batch_alter_table('questions', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_questions_topic'))
        batch_op.drop_index(batch_op.f('ix_questions_subject'))
        batch_op.drop_index(batch_op.f('ix_questions_channel_id'))
        batch_op.drop_index(batch_op.f('ix_questions_author_id'))

    op.drop_table('questions')
    with op.batch_alter_table('llm_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_llm_requests_user_id'))

    op.drop_table('llm_requests')
    with op.batch_alter_table('channel_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_channel_messages_parent_id'))
        batch_op.drop_index(batch_op.f('ix_channel_messages_channel_id'))

    op.drop_table('channel_messages')
    with op.batch_alter_table('channel_memberships', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_channel_memberships_user_id'))

    op.drop_table('channel_memberships')
    op.drop_table('user_blocks')
    op.drop_table('user_badges')
    with op.batch_alter_table('reputation_events', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_reputation_events_user_id'))

    op.drop_table('reputation_events')
    with op.batch_alter_table('notifications', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_notifications_user_id'))

    op.drop_table('notifications')
    op.drop_table('notification_preferences')
    with op.batch_alter_table('direct_messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_direct_messages_conversation_id'))

    op.drop_table('direct_messages')
    with op.batch_alter_table('conversation_participants', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_conversation_participants_user_id'))

    op.drop_table('conversation_participants')
    with op.batch_alter_table('channels', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_channels_subject'))
        batch_op.drop_index(batch_op.f('ix_channels_name'))
        batch_op.drop_index(batch_op.f('ix_channels_created_by'))

    op.drop_table('channels')
    with op.batch_alter_table('users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_users_email'))

    op.drop_table('users')
    op.drop_table('conversations')
    op.drop_table('badges')
