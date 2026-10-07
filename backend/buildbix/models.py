from datetime import datetime

from sqlalchemy import JSON, BigInteger, ForeignKey, Index, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .core.db import Base, utcnow


class User(Base):
    __tablename__ = "users"
    id: Mapped[int] = mapped_column(primary_key=True)
    email: Mapped[str] = mapped_column(String(254), unique=True, index=True)
    password_hash: Mapped[str] = mapped_column(String(300))
    token_version: Mapped[int] = mapped_column(default=0)  # bump to revoke all tokens
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Workspace(Base):
    __tablename__ = "workspaces"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    created_by: Mapped[int] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Membership(Base):
    __tablename__ = "memberships"
    __table_args__ = (UniqueConstraint("workspace_id", "user_id"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    role: Mapped[str] = mapped_column(String(20))  # viewer | developer | admin | owner


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    source_kind: Mapped[str] = mapped_column(String(10))  # zip | git
    repo_url: Mapped[str | None] = mapped_column(String(300))
    branch: Mapped[str | None] = mapped_column(String(100))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    source_key: Mapped[str] = mapped_column(String(300))
    analysis: Mapped[dict] = mapped_column(JSON, default=dict)
    webhook_secret_enc: Mapped[str | None] = mapped_column(Text)
    auto_build_target: Mapped[str | None] = mapped_column(String(40))
    auto_build_accept_license: Mapped[bool] = mapped_column(default=False)
    ci_params: Mapped[dict | None] = mapped_column(JSON)  # {template_id: {param: value}} approved by a user
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Build(Base):
    __tablename__ = "builds"
    __table_args__ = (UniqueConstraint("project_id", "number"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    number: Mapped[int]
    target: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(20), default="queued", index=True)
    # queued | running | success | failed | cancelled
    plan: Mapped[dict] = mapped_column(JSON, default=dict)
    source_key: Mapped[str] = mapped_column(String(300))
    commit_sha: Mapped[str | None] = mapped_column(String(64))
    version: Mapped[str | None] = mapped_column(String(50))
    trigger: Mapped[str] = mapped_column(String(20), default="manual")
    log_key: Mapped[str] = mapped_column(String(300))
    error_message: Mapped[str | None] = mapped_column(Text)
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class Artifact(Base):
    __tablename__ = "artifacts"
    id: Mapped[int] = mapped_column(primary_key=True)
    build_id: Mapped[int] = mapped_column(ForeignKey("builds.id", ondelete="CASCADE"), index=True)
    filename: Mapped[str] = mapped_column(String(200))
    storage_key: Mapped[str] = mapped_column(String(300))
    size: Mapped[int] = mapped_column(BigInteger)
    sha256: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Job(Base):
    __tablename__ = "jobs"
    __table_args__ = (Index("ix_jobs_claim", "status", "queue", "priority"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    kind: Mapped[str] = mapped_column(String(30))
    queue: Mapped[str] = mapped_column(String(30), default="build")
    status: Mapped[str] = mapped_column(String(20), default="queued")  # queued|running|done|failed|cancelled
    priority: Mapped[int] = mapped_column(default=0)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    attempts: Mapped[int] = mapped_column(default=0)
    max_attempts: Mapped[int] = mapped_column(default=1)
    cancel_requested: Mapped[bool] = mapped_column(default=False)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    started_at: Mapped[datetime | None]
    finished_at: Mapped[datetime | None]


class Automation(Base):
    __tablename__ = "automations"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    schedule_text: Mapped[str | None] = mapped_column(String(200))
    schedule: Mapped[dict | None] = mapped_column(JSON)
    event: Mapped[str | None] = mapped_column(String(20))  # push | tag | release (instead of a schedule)
    action: Mapped[str] = mapped_column(String(20), default="build")
    target: Mapped[str] = mapped_column(String(40))
    accept_sdk_license: Mapped[bool] = mapped_column(default=False)
    enabled: Mapped[bool] = mapped_column(default=True)
    next_run_at: Mapped[datetime | None] = mapped_column(index=True)
    last_run_at: Mapped[datetime | None]
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class Secret(Base):
    __tablename__ = "secrets"
    __table_args__ = (UniqueConstraint("workspace_id", "name", "environment"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(64))
    environment: Mapped[str] = mapped_column(String(20), default="production")
    value_enc: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AuditLog(Base):
    __tablename__ = "audit_logs"
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int | None] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    actor_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    event: Mapped[str] = mapped_column(String(60), index=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class AIMessage(Base):
    __tablename__ = "ai_messages"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    user_id: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    role: Mapped[str] = mapped_column(String(10))  # user | assistant
    content: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(default=utcnow)


class FixProposal(Base):
    """An Auto-Fix suggestion. Nothing is changed until a user explicitly applies it."""
    __tablename__ = "fix_proposals"
    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id", ondelete="CASCADE"), index=True)
    build_id: Mapped[int | None] = mapped_column(ForeignKey("builds.id", ondelete="SET NULL"))
    code: Mapped[str] = mapped_column(String(60))
    title: Mapped[str] = mapped_column(String(200))
    explanation: Mapped[str] = mapped_column(Text)
    edits: Mapped[list] = mapped_column(JSON, default=list)
    status: Mapped[str] = mapped_column(String(20), default="proposed")  # proposed | applied | rejected
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
    applied_at: Mapped[datetime | None]


class Server(Base):
    __tablename__ = "servers"
    __table_args__ = (UniqueConstraint("workspace_id", "name"),)
    id: Mapped[int] = mapped_column(primary_key=True)
    workspace_id: Mapped[int] = mapped_column(ForeignKey("workspaces.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(63))
    provider: Mapped[str] = mapped_column(String(30))
    provider_id: Mapped[str] = mapped_column(String(64))
    server_type: Mapped[str] = mapped_column(String(40))
    region: Mapped[str] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(30))
    ip: Mapped[str | None] = mapped_column(String(64))
    created_by: Mapped[int | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[datetime] = mapped_column(default=utcnow)
