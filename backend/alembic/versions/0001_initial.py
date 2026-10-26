"""Esquema inicial: users, rooms, bookings (constraint GiST anti-sobreposição), booking_logs.

Revision ID: 0001_initial
Revises:
Create Date: 2026-10-23
"""

from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.execute("CREATE EXTENSION IF NOT EXISTS btree_gist")

    op.execute(
        """
        CREATE TABLE users (
            id            SERIAL PRIMARY KEY,
            username      VARCHAR(50)  NOT NULL,
            full_name     VARCHAR(150) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            role          VARCHAR(20)  NOT NULL DEFAULT 'RECEPTIONIST'
                          CONSTRAINT ck_users_role CHECK (role IN ('ADMIN', 'RECEPTIONIST')),
            is_active     BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at    TIMESTAMPTZ  NOT NULL DEFAULT now(),
            last_login_at TIMESTAMPTZ
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_users_username_lower ON users (lower(username))")

    op.execute(
        """
        CREATE TABLE rooms (
            id                    SERIAL PRIMARY KEY,
            name                  VARCHAR(100) NOT NULL,
            room_type             VARCHAR(20)  NOT NULL
                                  CONSTRAINT ck_rooms_type CHECK (room_type IN ('VIDEO', 'NORMAL')),
            capacity              INTEGER      NOT NULL DEFAULT 0
                                  CONSTRAINT ck_rooms_capacity CHECK (capacity >= 0),
            resources             JSONB        NOT NULL DEFAULT '[]'::jsonb,
            video_status          VARCHAR(20)  NOT NULL DEFAULT 'OPERATIONAL'
                                  CONSTRAINT ck_rooms_video_status
                                  CHECK (video_status IN ('OPERATIONAL', 'MAINTENANCE')),
            maintenance_ticket_id VARCHAR(50),
            maintenance_since     TIMESTAMPTZ,
            display_order         INTEGER      NOT NULL DEFAULT 0,
            is_active             BOOLEAN      NOT NULL DEFAULT TRUE,
            created_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at            TIMESTAMPTZ  NOT NULL DEFAULT now(),
            CONSTRAINT ck_rooms_maintenance_only_video
                CHECK (room_type = 'VIDEO' OR video_status = 'OPERATIONAL')
        )
        """
    )
    op.execute("CREATE UNIQUE INDEX uq_rooms_name_lower ON rooms (lower(name))")

    op.execute(
        """
        CREATE TABLE bookings (
            id             UUID         PRIMARY KEY DEFAULT gen_random_uuid(),
            room_id        INTEGER      NOT NULL REFERENCES rooms (id),
            ticket_number  VARCHAR(50),
            title          VARCHAR(200),
            requester_name VARCHAR(150) NOT NULL,
            notes          TEXT,
            attendees      INTEGER
                           CONSTRAINT ck_bookings_attendees CHECK (attendees IS NULL OR attendees > 0),
            start_time     TIMESTAMPTZ  NOT NULL,
            end_time       TIMESTAMPTZ  NOT NULL,
            period_type    VARCHAR(20)  NOT NULL DEFAULT 'CUSTOM'
                           CONSTRAINT ck_bookings_period
                           CHECK (period_type IN ('CUSTOM', 'FULL_DAY', 'MORNING', 'AFTERNOON')),
            is_full_day    BOOLEAN      NOT NULL DEFAULT FALSE,
            requires_video BOOLEAN      NOT NULL DEFAULT FALSE,
            status         VARCHAR(20)  NOT NULL DEFAULT 'CONFIRMED'
                           CONSTRAINT ck_bookings_status
                           CHECK (status IN ('CONFIRMED', 'CHECKED_IN', 'CANCELLED', 'NO_SHOW')),
            version        INTEGER      NOT NULL DEFAULT 1,
            created_by     INTEGER      NOT NULL REFERENCES users (id),
            updated_by     INTEGER      REFERENCES users (id),
            created_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
            updated_at     TIMESTAMPTZ  NOT NULL DEFAULT now(),
            cancelled_at   TIMESTAMPTZ,
            cancelled_by   INTEGER      REFERENCES users (id),
            cancel_reason  VARCHAR(500),
            CONSTRAINT ck_bookings_interval CHECK (end_time > start_time),
            -- RNF02: duas reservas ativas da mesma sala nunca se sobrepõem, nem em corrida.
            CONSTRAINT bookings_no_overlap EXCLUDE USING gist (
                room_id WITH =,
                tstzrange(start_time, end_time, '[)') WITH &&
            ) WHERE (status <> 'CANCELLED')
        )
        """
    )
    op.execute("CREATE INDEX ix_bookings_start_time ON bookings (start_time)")
    op.execute("CREATE INDEX ix_bookings_ticket_number ON bookings (ticket_number)")
    op.execute("CREATE INDEX ix_bookings_room_start ON bookings (room_id, start_time)")

    op.execute(
        """
        CREATE TABLE booking_logs (
            id             BIGSERIAL    PRIMARY KEY,
            booking_id     UUID         NOT NULL REFERENCES bookings (id) ON DELETE CASCADE,
            user_id        INTEGER      REFERENCES users (id),
            action         VARCHAR(20)  NOT NULL
                           CONSTRAINT ck_booking_logs_action CHECK (action IN (
                               'CREATED', 'IMPORTED', 'UPDATED', 'MOVED_ROOM',
                               'RESCHEDULED', 'STATUS_CHANGED', 'CANCELLED')),
            previous_state JSONB,
            new_state      JSONB,
            created_at     TIMESTAMPTZ  NOT NULL DEFAULT now()
        )
        """
    )
    op.execute("CREATE INDEX ix_booking_logs_booking ON booking_logs (booking_id, created_at)")
    op.execute("CREATE INDEX ix_booking_logs_created ON booking_logs (created_at)")


def downgrade() -> None:
    op.execute("DROP TABLE IF EXISTS booking_logs")
    op.execute("DROP TABLE IF EXISTS bookings")
    op.execute("DROP TABLE IF EXISTS rooms")
    op.execute("DROP TABLE IF EXISTS users")
