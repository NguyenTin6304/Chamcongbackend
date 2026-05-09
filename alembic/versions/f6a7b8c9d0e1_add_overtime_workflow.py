"""add overtime workflow tables and rule fields

Revision ID: f6a7b8c9d0e1
Revises: e5f6a7b8c9d0
Create Date: 2026-05-05
"""

from alembic import op
import sqlalchemy as sa


revision = 'f6a7b8c9d0e1'
down_revision = 'e5f6a7b8c9d0'
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column('checkin_rules', sa.Column(
        'overtime_enabled', sa.Boolean(), nullable=False, server_default='true',
    ))
    op.add_column('checkin_rules', sa.Column(
        'overtime_minimum_minutes', sa.Integer(), nullable=False, server_default='30',
    ))

    op.create_table(
        'overtime_records',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('employee_id', sa.Integer(),
                  sa.ForeignKey('employees.id'), nullable=False, index=True),
        sa.Column('work_date', sa.Date(), nullable=False, index=True),
        sa.Column('attendance_log_id', sa.Integer(),
                  sa.ForeignKey('attendance_logs.id'), nullable=True),
        sa.Column('raw_minutes', sa.Integer(), nullable=False),
        sa.Column('approved_minutes', sa.Integer(), nullable=True),
        sa.Column('status', sa.String(20), nullable=False, server_default='PENDING'),
        sa.Column('source', sa.String(30), nullable=False, server_default='AUTO_CHECKOUT'),
        sa.Column('employee_note', sa.Text(), nullable=True),
        sa.Column('admin_id', sa.Integer(), sa.ForeignKey('users.id'), nullable=True),
        sa.Column('admin_note', sa.Text(), nullable=True),
        sa.Column('decided_at', sa.DateTime(timezone=True), nullable=True),
        sa.Column('shift_start_snapshot', sa.Time(), nullable=True),
        sa.Column('shift_end_snapshot', sa.Time(), nullable=True),
        sa.Column('is_weekend', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('is_holiday', sa.Boolean(), nullable=False, server_default='false'),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
        sa.UniqueConstraint('employee_id', 'work_date',
                            name='uq_overtime_employee_workdate'),
    )
    op.create_index('ix_overtime_records_status',
                    'overtime_records', ['status'])

    op.create_table(
        'overtime_audits',
        sa.Column('id', sa.Integer(), primary_key=True),
        sa.Column('overtime_id', sa.Integer(),
                  sa.ForeignKey('overtime_records.id', ondelete='CASCADE'),
                  nullable=False, index=True),
        sa.Column('action', sa.String(30), nullable=False),
        sa.Column('actor_id', sa.Integer(),
                  sa.ForeignKey('users.id'), nullable=True),
        sa.Column('from_status', sa.String(20), nullable=True),
        sa.Column('to_status', sa.String(20), nullable=True),
        sa.Column('from_minutes', sa.Integer(), nullable=True),
        sa.Column('to_minutes', sa.Integer(), nullable=True),
        sa.Column('note', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True),
                  server_default=sa.func.now(), nullable=False),
    )


def downgrade() -> None:
    op.drop_table('overtime_audits')
    op.drop_index('ix_overtime_records_status', table_name='overtime_records')
    op.drop_table('overtime_records')
    op.drop_column('checkin_rules', 'overtime_minimum_minutes')
    op.drop_column('checkin_rules', 'overtime_enabled')
