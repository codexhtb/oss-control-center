"""Controlled retention of unlinked lookup snapshots.

Revision ID: 0006_lookup_cleanup
Revises: 0005_netup_groups
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006_lookup_cleanup"
down_revision: str | None = "0005_netup_groups"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "lookup_cleanup_audit",
        sa.Column("id", sa.BigInteger(), sa.Identity(), primary_key=True),
        sa.Column(
            "ran_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("cutoff_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("deleted_count", sa.Integer(), nullable=False),
        sa.CheckConstraint("deleted_count >= 0", name="ck_lookup_cleanup_count"),
    )
    op.create_index("ix_subscriber_snapshots_fetched", "subscriber_snapshots", ["fetched_at", "id"])
    op.create_index("ix_tickets_subscriber_snapshot", "tickets", ["subscriber_snapshot_id"])
    for table in ("subscriber_snapshots", "connection_snapshots"):
        op.execute(f"DROP TRIGGER immutable_{table} ON {table}")
    op.execute(
        """CREATE FUNCTION lookup_snapshot_delete_guard() RETURNS trigger
        LANGUAGE plpgsql SET search_path = pg_catalog, public AS $$
        BEGIN
            IF TG_OP = 'DELETE' AND current_setting('oss.lookup_cleanup', true) = 'on' THEN
                IF TG_TABLE_NAME = 'subscriber_snapshots' THEN
                    IF OLD.fetched_at < clock_timestamp() - interval '24 hours'
                       AND NOT EXISTS (SELECT 1 FROM public.tickets t
                                       WHERE t.subscriber_snapshot_id = OLD.id) THEN
                        RETURN OLD;
                    END IF;
                ELSIF TG_TABLE_NAME = 'connection_snapshots' THEN
                    IF EXISTS (SELECT 1 FROM public.subscriber_snapshots s
                               WHERE s.id = OLD.subscriber_snapshot_id
                                 AND s.fetched_at < clock_timestamp() - interval '24 hours'
                                 AND NOT EXISTS (SELECT 1 FROM public.tickets t
                                                 WHERE t.subscriber_snapshot_id = s.id)) THEN
                        RETURN OLD;
                    END IF;
                END IF;
            END IF;
            RAISE EXCEPTION USING MESSAGE = 'immutable Service Desk record', ERRCODE = '23514';
        END $$"""
    )
    for table in ("subscriber_snapshots", "connection_snapshots"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION lookup_snapshot_delete_guard()"
        )
    op.execute(
        """CREATE TRIGGER immutable_lookup_cleanup_audit
        BEFORE UPDATE OR DELETE ON lookup_cleanup_audit
        FOR EACH ROW EXECUTE FUNCTION ticket_records_immutable()"""
    )
    op.execute(
        """CREATE FUNCTION purge_expired_lookup_snapshots(p_limit integer DEFAULT 500)
        RETURNS integer LANGUAGE plpgsql SECURITY DEFINER
        SET search_path = pg_catalog, public AS $$
        DECLARE
            ids uuid[];
            removed integer := 0;
            cutoff timestamptz := clock_timestamp() - interval '24 hours';
        BEGIN
            IF p_limit IS NULL OR p_limit < 1 OR p_limit > 500 THEN
                RAISE EXCEPTION USING MESSAGE = 'invalid cleanup batch limit', ERRCODE = '22023';
            END IF;
            SELECT array_agg(candidate.id) INTO ids FROM (
                SELECT s.id FROM public.subscriber_snapshots s
                WHERE s.fetched_at < cutoff
                  AND NOT EXISTS (SELECT 1 FROM public.tickets t
                                  WHERE t.subscriber_snapshot_id = s.id)
                ORDER BY s.fetched_at, s.id LIMIT p_limit
                FOR UPDATE OF s SKIP LOCKED
            ) candidate;
            IF ids IS NOT NULL THEN
                PERFORM set_config('oss.lookup_cleanup', 'on', true);
                DELETE FROM public.connection_snapshots c
                WHERE c.subscriber_snapshot_id = ANY(ids);
                DELETE FROM public.subscriber_snapshots s
                WHERE s.id = ANY(ids)
                  AND NOT EXISTS (SELECT 1 FROM public.tickets t
                                  WHERE t.subscriber_snapshot_id = s.id);
                GET DIAGNOSTICS removed = ROW_COUNT;
                PERFORM set_config('oss.lookup_cleanup', 'off', true);
            END IF;
            INSERT INTO public.lookup_cleanup_audit(cutoff_at, deleted_count)
            VALUES (cutoff, removed);
            RETURN removed;
        END $$"""
    )
    op.execute("REVOKE ALL ON FUNCTION purge_expired_lookup_snapshots(integer) FROM PUBLIC")


def downgrade() -> None:
    op.execute("DROP FUNCTION purge_expired_lookup_snapshots(integer)")
    op.execute("DROP TRIGGER immutable_lookup_cleanup_audit ON lookup_cleanup_audit")
    for table in ("subscriber_snapshots", "connection_snapshots"):
        op.execute(f"DROP TRIGGER immutable_{table} ON {table}")
    op.execute("DROP FUNCTION lookup_snapshot_delete_guard()")
    for table in ("subscriber_snapshots", "connection_snapshots"):
        op.execute(
            f"CREATE TRIGGER immutable_{table} BEFORE UPDATE OR DELETE ON {table} "
            "FOR EACH ROW EXECUTE FUNCTION ticket_records_immutable()"
        )
    op.drop_index("ix_tickets_subscriber_snapshot", table_name="tickets")
    op.drop_index("ix_subscriber_snapshots_fetched", table_name="subscriber_snapshots")
    op.drop_table("lookup_cleanup_audit")
