import uuid
from datetime import datetime, timezone

from sqlalchemy import JSON, Boolean, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


def utcnow():
    return datetime.now(timezone.utc)


def new_id():
    return uuid.uuid4().hex


class Base(DeclarativeBase):
    pass


STATUSES = ("queued", "running", "answered", "reported", "failed")
FIX_STATUSES = ("awaiting_approval", "approved", "running", "pr_open", "merged", "failed", "declined")


class Investigation(Base):
    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    status: Mapped[str] = mapped_column(String(16), default="queued", index=True)
    question: Mapped[str] = mapped_column(Text)
    org_id: Mapped[str | None] = mapped_column(String(18))
    record_id: Mapped[str | None] = mapped_column(String(18), index=True)
    record_object: Mapped[str | None] = mapped_column(String(80))
    record_url: Mapped[str | None] = mapped_column(String(512))
    record_context: Mapped[dict | None] = mapped_column(JSON)
    asked_by: Mapped[dict | None] = mapped_column(JSON)          # {sf_user_id, name, email}

    progress: Mapped[str | None] = mapped_column(String(255))
    answer_markdown: Mapped[str | None] = mapped_column(Text)
    answer_html: Mapped[str | None] = mapped_column(Text)
    report_markdown: Mapped[str | None] = mapped_column(Text)
    findings: Mapped[dict | None] = mapped_column(JSON)          # structured output from the investigator
    confidence: Mapped[str | None] = mapped_column(String(8))
    needs_fix: Mapped[bool] = mapped_column(Boolean, default=False)
    cost_usd: Mapped[float | None] = mapped_column(Float)
    num_turns: Mapped[int | None] = mapped_column(Integer)
    claude_session_id: Mapped[str | None] = mapped_column(String(64))
    error: Mapped[str | None] = mapped_column(Text)
    worker: Mapped[str | None] = mapped_column(String(120))

    # Reporting to engineering
    reporter_comment: Mapped[str | None] = mapped_column(Text)
    reported_by: Mapped[dict | None] = mapped_column(JSON)
    issue_repo: Mapped[str | None] = mapped_column(String(200))
    issue_number: Mapped[int | None] = mapped_column(Integer, index=True)
    issue_url: Mapped[str | None] = mapped_column(String(512))

    # Fixing
    fix_status: Mapped[str | None] = mapped_column(String(24), index=True)
    fix_repo: Mapped[str | None] = mapped_column(String(200))
    fix_branch: Mapped[str | None] = mapped_column(String(200))
    fix_pr_url: Mapped[str | None] = mapped_column(String(512))
    fix_pr_number: Mapped[int | None] = mapped_column(Integer)
    fix_summary: Mapped[dict | None] = mapped_column(JSON)
    fix_cost_usd: Mapped[float | None] = mapped_column(Float)
    fix_error: Mapped[str | None] = mapped_column(Text)
    approved_by: Mapped[str | None] = mapped_column(String(120))
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    events: Mapped[list["InvestigationEvent"]] = relationship(back_populates="investigation", cascade="all, delete-orphan", order_by="InvestigationEvent.id")

    # ---- serialisation -------------------------------------------------
    def to_api(self):
        """Shape returned to Salesforce (ClaudeTraceService.parse mirrors this)."""
        return {
            "id": self.id,
            "status": self.status,
            "progress": self.progress,
            "question": self.question,
            "answer_markdown": self.answer_markdown,
            "answer_html": self.answer_html,
            "report_markdown": self.report_markdown,
            "confidence": self.confidence,
            "needs_fix": bool(self.needs_fix),
            "cost_usd": self.cost_usd,
            "error": self.error,
            "issue": {"url": self.issue_url, "number": self.issue_number, "repo": self.issue_repo} if self.issue_url else None,
            "fix": {"status": self.fix_status, "pr_url": self.fix_pr_url, "repo": self.fix_repo, "branch": self.fix_branch} if self.fix_status else None,
            "created_at": iso(self.created_at),
            "finished_at": iso(self.finished_at),
        }

    def to_admin(self):
        d = self.to_api()
        d.update({
            "record_id": self.record_id, "record_object": self.record_object, "record_url": self.record_url,
            "record_context": self.record_context, "asked_by": self.asked_by, "reporter_comment": self.reporter_comment,
            "reported_by": self.reported_by, "findings": self.findings, "num_turns": self.num_turns,
            "fix_summary": self.fix_summary, "fix_cost_usd": self.fix_cost_usd, "fix_error": self.fix_error,
            "approved_by": self.approved_by, "approved_at": iso(self.approved_at), "started_at": iso(self.started_at),
            "updated_at": iso(self.updated_at), "worker": self.worker,
        })
        return d


class InvestigationEvent(Base):
    """What Claude did, one line at a time (shown in the admin UI and useful for debugging)."""
    __tablename__ = "investigation_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    investigation_id: Mapped[str] = mapped_column(ForeignKey("investigations.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    phase: Mapped[str] = mapped_column(String(16))      # investigate | fix | system
    kind: Mapped[str] = mapped_column(String(32))       # tool | text | status | error
    text: Mapped[str] = mapped_column(Text)

    investigation: Mapped[Investigation] = relationship(back_populates="events")

    def to_dict(self):
        return {"id": self.id, "at": iso(self.at), "phase": self.phase, "kind": self.kind, "text": self.text}


def iso(dt):
    return dt.isoformat() if dt else None
