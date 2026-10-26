"""users.token_version (revogação de tokens) e bookings.import_key (reimportação idempotente).

Revision ID: 0002_token_version_import_key
Revises: 0001_initial
Create Date: 2026-10-26
"""

from alembic import op

revision = "0002_token_version_import_key"
down_revision = "0001_initial"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("ALTER TABLE users ADD COLUMN token_version INTEGER NOT NULL DEFAULT 0")
    op.execute("ALTER TABLE bookings ADD COLUMN import_key VARCHAR(100)")
    op.execute(
        "CREATE INDEX ix_bookings_import_key ON bookings (import_key) WHERE import_key IS NOT NULL"
    )


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_bookings_import_key")
    op.execute("ALTER TABLE bookings DROP COLUMN IF EXISTS import_key")
    op.execute("ALTER TABLE users DROP COLUMN IF EXISTS token_version")
