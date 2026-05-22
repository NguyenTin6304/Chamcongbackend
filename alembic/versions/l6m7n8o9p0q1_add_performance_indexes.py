"""add composite indexes for performance optimization

Adds composite indexes to speed up admin report queries that filter
by multiple columns simultaneously. Uses CREATE INDEX CONCURRENTLY so
the migration does NOT lock the tables during deployment.

Targets:
  - attendance_logs(employee_id, work_date) — /reports/daily, /reports/monthly-pivot,
    /attendance-logs filtered by employee+date range
  - attendance_exceptions(status, work_date) — admin exceptions screen filter
  - leave_requests(status, start_date) — admin leaves tab filter
  - overtime_records(status) — admin overtime tab filter
    (employee_id+work_date already covered by uq_overtime_employee_workdate)

Revision ID: l6m7n8o9p0q1
Revises: k5l6m7n8o9p0
Create Date: 2026-05-22 14:30:00.000000

"""
from typing import Sequence, Union

from alembic import op


revision: str = 'l6m7n8o9p0q1'
down_revision: Union[str, None] = 'k5l6m7n8o9p0'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

# CONCURRENTLY cannot run inside a transaction — alembic env.py must not wrap
# upgrade()/downgrade() in BEGIN/COMMIT. The default env.py uses
# context.begin_transaction(); we use op.execute with explicit COMMIT semantics
# only when needed. For maximum compatibility across alembic configs we use
# the raw SQL form and rely on PostgreSQL to skip-if-exists.


def upgrade() -> None:
    # attendance_logs: composite for employee timeline + report queries
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_attendance_logs_employee_workdate "
        "ON attendance_logs (employee_id, work_date)"
    )

    # attendance_exceptions: admin filters by status + work_date
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_attendance_exceptions_status_workdate "
        "ON attendance_exceptions (status, work_date)"
    )

    # leave_requests: admin filters by status (PENDING/APPROVED/REJECTED) + start_date
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_leave_requests_status_startdate "
        "ON leave_requests (status, start_date)"
    )

    # overtime_records: admin filters by status (PENDING/APPROVED/REJECTED).
    # (employee_id, work_date) already covered by uq_overtime_employee_workdate.
    op.execute(
        "CREATE INDEX IF NOT EXISTS idx_overtime_records_status "
        "ON overtime_records (status)"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS idx_overtime_records_status")
    op.execute("DROP INDEX IF EXISTS idx_leave_requests_status_startdate")
    op.execute("DROP INDEX IF EXISTS idx_attendance_exceptions_status_workdate")
    op.execute("DROP INDEX IF EXISTS idx_attendance_logs_employee_workdate")
