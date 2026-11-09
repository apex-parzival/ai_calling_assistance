"""initial schema

Revision ID: 0001
Revises: 
Create Date: 2026-11-07 13:43:00.490994
"""
from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

revision = '0001'
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:

    op.create_table('audit_log',
    sa.Column('id', sa.Integer(), autoincrement=True, nullable=False),
    sa.Column('at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('actor_type', sa.String(length=16), nullable=False),
    sa.Column('actor_id', sa.String(length=64), nullable=True),
    sa.Column('action', sa.String(length=32), nullable=False),
    sa.Column('entity', sa.String(length=64), nullable=False),
    sa.Column('entity_id', sa.String(length=64), nullable=True),
    sa.Column('details', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('ip', sa.String(length=64), nullable=True),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_audit_log_actor_id'), ['actor_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_at'), ['at'], unique=False)
        batch_op.create_index(batch_op.f('ix_audit_log_entity_id'), ['entity_id'], unique=False)

    op.create_table('staff_users',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('name', sa.String(length=120), nullable=False),
    sa.Column('email', sa.String(length=254), nullable=False),
    sa.Column('password_hash', sa.String(length=255), nullable=False),
    sa.Column('role', sa.Enum('admin', 'staff', name='staffrole', native_enum=False, length=32), nullable=False),
    sa.Column('is_active', sa.Boolean(), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('staff_users', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_staff_users_email'), ['email'], unique=True)

    op.create_table('calls',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('room_name', sa.String(length=128), nullable=True),
    sa.Column('caller_name', sa.String(length=120), nullable=True),
    sa.Column('caller_phone', sa.String(length=32), nullable=True),
    sa.Column('started_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('ended_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('duration', sa.Integer(), nullable=True),
    sa.Column('reason', sa.String(length=255), nullable=True),
    sa.Column('urgency', sa.Enum('routine', 'urgent', 'emergency', name='urgency', native_enum=False, length=32), nullable=False),
    sa.Column('summary', sa.Text(), nullable=True),
    sa.Column('transcript', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('recording_url', sa.String(length=1024), nullable=True),
    sa.Column('status', sa.Enum('New', 'In progress', 'Handled', name='callstatus', native_enum=False, length=32), nullable=False),
    sa.Column('notes', sa.Text(), nullable=True),
    sa.Column('assigned_to_id', sa.Uuid(), nullable=True),
    sa.Column('latency', sa.JSON().with_variant(postgresql.JSONB(astext_type=sa.Text()), 'postgresql'), nullable=True),
    sa.Column('outcome', sa.String(length=64), nullable=True),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['assigned_to_id'], ['staff_users.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_calls_caller_phone'), ['caller_phone'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_room_name'), ['room_name'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_started_at'), ['started_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_status'), ['status'], unique=False)
        batch_op.create_index(batch_op.f('ix_calls_urgency'), ['urgency'], unique=False)

    op.create_table('appointment_requests',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('call_id', sa.Uuid(), nullable=True),
    sa.Column('kind', sa.Enum('book', 'cancel', name='requestkind', native_enum=False, length=32), nullable=False),
    sa.Column('patient_name', sa.String(length=120), nullable=True),
    sa.Column('patient_phone', sa.String(length=32), nullable=False),
    sa.Column('requested_date', sa.Date(), nullable=False),
    sa.Column('requested_time', sa.Time(), nullable=True),
    sa.Column('service', sa.String(length=64), nullable=True),
    sa.Column('booking_status', sa.Enum('requested', 'confirmed', 'declined', 'cancel_requested', 'cancelled', name='bookingstatus', native_enum=False, length=32), nullable=False),
    sa.Column('external_ref', sa.String(length=128), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('updated_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('appointment_requests', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_appointment_requests_booking_status'), ['booking_status'], unique=False)
        batch_op.create_index(batch_op.f('ix_appointment_requests_call_id'), ['call_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_appointment_requests_patient_phone'), ['patient_phone'], unique=False)
        batch_op.create_index(batch_op.f('ix_appointment_requests_requested_date'), ['requested_date'], unique=False)

    op.create_table('messages',
    sa.Column('id', sa.Uuid(), nullable=False),
    sa.Column('call_id', sa.Uuid(), nullable=True),
    sa.Column('caller_name', sa.String(length=120), nullable=True),
    sa.Column('caller_phone', sa.String(length=32), nullable=True),
    sa.Column('reason', sa.Text(), nullable=False),
    sa.Column('urgency', sa.Enum('routine', 'urgent', 'emergency', name='urgency', native_enum=False, length=32), nullable=False),
    sa.Column('status', sa.Enum('New', 'Handled', name='messagestatus', native_enum=False, length=32), nullable=False),
    sa.Column('created_at', sa.DateTime(timezone=True), nullable=False),
    sa.ForeignKeyConstraint(['call_id'], ['calls.id'], ondelete='SET NULL'),
    sa.PrimaryKeyConstraint('id')
    )
    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.create_index(batch_op.f('ix_messages_call_id'), ['call_id'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_created_at'), ['created_at'], unique=False)
        batch_op.create_index(batch_op.f('ix_messages_status'), ['status'], unique=False)




def downgrade() -> None:

    with op.batch_alter_table('messages', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_messages_status'))
        batch_op.drop_index(batch_op.f('ix_messages_created_at'))
        batch_op.drop_index(batch_op.f('ix_messages_call_id'))

    op.drop_table('messages')
    with op.batch_alter_table('appointment_requests', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_appointment_requests_requested_date'))
        batch_op.drop_index(batch_op.f('ix_appointment_requests_patient_phone'))
        batch_op.drop_index(batch_op.f('ix_appointment_requests_call_id'))
        batch_op.drop_index(batch_op.f('ix_appointment_requests_booking_status'))

    op.drop_table('appointment_requests')
    with op.batch_alter_table('calls', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_calls_urgency'))
        batch_op.drop_index(batch_op.f('ix_calls_status'))
        batch_op.drop_index(batch_op.f('ix_calls_started_at'))
        batch_op.drop_index(batch_op.f('ix_calls_room_name'))
        batch_op.drop_index(batch_op.f('ix_calls_caller_phone'))

    op.drop_table('calls')
    with op.batch_alter_table('staff_users', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_staff_users_email'))

    op.drop_table('staff_users')
    with op.batch_alter_table('audit_log', schema=None) as batch_op:
        batch_op.drop_index(batch_op.f('ix_audit_log_entity_id'))
        batch_op.drop_index(batch_op.f('ix_audit_log_at'))
        batch_op.drop_index(batch_op.f('ix_audit_log_actor_id'))

    op.drop_table('audit_log')

