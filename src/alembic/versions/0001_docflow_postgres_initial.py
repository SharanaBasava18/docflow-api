"""Create the initial DocFlow PostgreSQL schema.

Revision ID: 0001_docflow_postgres
Revises:
Create Date: 2026-06-21
"""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql


revision = "0001_docflow_postgres"
down_revision = None
branch_labels = None
depends_on = None


file_status = postgresql.ENUM("pending", "processing", "available", "failed", "deleted", name="file_status", create_type=False)
organization_role = postgresql.ENUM("owner", "admin", "member", name="organization_role", create_type=False)
upload_session_status = postgresql.ENUM(
    "initiated", "uploading", "ready_to_complete", "assembling", "completed", "failed", "expired", "cancelled",
    name="upload_session_status", create_type=False,
)


def upgrade() -> None:
    bind = op.get_bind()
    organization_role.create(bind, checkfirst=True)
    file_status.create(bind, checkfirst=True)
    upload_session_status.create(bind, checkfirst=True)

    op.create_table(
        "users",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("email", sa.String(320), nullable=False),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("hashed_password", sa.String(255), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False, server_default=sa.text("true")),
        sa.Column("is_verified", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.UniqueConstraint("email", name="uq_users_email"),
    )
    op.create_index("ix_users_email", "users", ["email"])

    op.create_table(
        "organizations",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("slug", sa.String(100), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("slug", name="uq_organizations_slug"),
    )
    op.create_index("ix_organizations_slug", "organizations", ["slug"])
    op.create_index("ix_organizations_owner_user_id", "organizations", ["owner_user_id"])

    op.create_table(
        "organization_members",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("role", organization_role, nullable=False, server_default="member"),
        sa.Column("joined_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("organization_id", "user_id", name="uq_organization_members_organization_user"),
    )
    op.create_index("ix_organization_members_organization_id", "organization_members", ["organization_id"])
    op.create_index("ix_organization_members_user_id", "organization_members", ["user_id"])

    op.create_table(
        "files",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("owner_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("stored_filename", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("storage_bucket", sa.String(255), nullable=False),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("status", file_status, nullable=False, server_default="pending"),
        sa.Column("file_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("processing_task_id", sa.String(255)),
        sa.Column("is_deleted", sa.Boolean(), nullable=False, server_default=sa.text("false")),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["owner_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("storage_bucket", "storage_key", name="uq_files_storage_location"),
    )
    op.create_index("ix_files_organization_id", "files", ["organization_id"])
    op.create_index("ix_files_owner_user_id", "files", ["owner_user_id"])
    op.create_index("ix_files_processing_task_id", "files", ["processing_task_id"])

    op.create_table(
        "upload_sessions",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("created_by_user_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("original_filename", sa.String(512), nullable=False),
        sa.Column("content_type", sa.String(255), nullable=False),
        sa.Column("expected_size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("expected_chunk_count", sa.Integer(), nullable=False),
        sa.Column("chunk_size_bytes", sa.Integer(), nullable=False),
        sa.Column("status", upload_session_status, nullable=False, server_default="initiated"),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["created_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="RESTRICT"),
    )
    for name, column in (("ix_upload_sessions_organization_id", "organization_id"), ("ix_upload_sessions_created_by_user_id", "created_by_user_id"), ("ix_upload_sessions_file_id", "file_id"), ("ix_upload_sessions_expires_at", "expires_at")):
        op.create_index(name, "upload_sessions", [column])

    op.create_table(
        "file_chunks",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("upload_session_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("chunk_index", sa.Integer(), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("checksum_sha256", sa.String(64)),
        sa.Column("storage_key", sa.String(1024), nullable=False),
        sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["upload_session_id"], ["upload_sessions.id"], ondelete="CASCADE"),
        sa.UniqueConstraint("upload_session_id", "chunk_index", name="uq_file_chunks_session_index"),
    )
    op.create_index("ix_file_chunks_upload_session_id", "file_chunks", ["upload_session_id"])

    op.create_table(
        "file_access_logs",
        sa.Column("id", postgresql.UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column("file_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("organization_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("actor_user_id", postgresql.UUID(as_uuid=True)),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("ip_address", postgresql.INET()),
        sa.Column("user_agent", sa.String(1024)),
        sa.Column("extra_metadata", postgresql.JSONB(), nullable=False, server_default=sa.text("'{}'::jsonb")),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.text("now()")),
        sa.ForeignKeyConstraint(["file_id"], ["files.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["organization_id"], ["organizations.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["actor_user_id"], ["users.id"], ondelete="SET NULL"),
    )
    for name, column in (("ix_file_access_logs_file_id", "file_id"), ("ix_file_access_logs_organization_id", "organization_id"), ("ix_file_access_logs_actor_user_id", "actor_user_id"), ("ix_file_access_logs_occurred_at", "occurred_at")):
        op.create_index(name, "file_access_logs", [column])


def downgrade() -> None:
    op.drop_table("file_access_logs")
    op.drop_table("file_chunks")
    op.drop_table("upload_sessions")
    op.drop_table("files")
    op.drop_table("organization_members")
    op.drop_table("organizations")
    op.drop_table("users")
    bind = op.get_bind()
    upload_session_status.drop(bind, checkfirst=True)
    file_status.drop(bind, checkfirst=True)
    organization_role.drop(bind, checkfirst=True)
