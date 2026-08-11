from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text, create_engine
from sqlalchemy.orm import declarative_base, relationship, sessionmaker

Base = declarative_base()


class Category(str, Enum):
    correctness = "correctness"
    security = "security"
    performance = "performance"
    testing = "testing"
    maintainability = "maintainability"


class Severity(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"
    critical = "critical"


class RiskLevel(str, Enum):
    low = "low"
    medium = "medium"
    high = "high"


class Finding(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str = Field(..., min_length=3, max_length=200)
    category: Category
    severity: Severity
    confidence: float = Field(..., ge=0.0, le=1.0)
    file_path: str = Field(..., min_length=1)
    line: Optional[int] = Field(None, ge=0)
    explanation: str = Field(..., min_length=10, max_length=2000)
    failure_scenario: str = Field(..., min_length=10, max_length=2000)
    suggestion: str = Field(..., min_length=5, max_length=2000)

    @field_validator("line")
    @classmethod
    def _normalize_line(cls, v: Optional[int]) -> Optional[int]:
        if v == 0:
            return None
        return v


class ReviewResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    summary: str = Field(..., min_length=20, max_length=2000)
    risk_level: RiskLevel
    findings: list[Finding] = Field(default_factory=list)
    suggested_tests: list[str] = Field(default_factory=list)

    @field_validator("findings")
    @classmethod
    def _limit_candidate_findings(cls, findings: list[Finding]) -> list[Finding]:
        return findings[:8]


# ------------------------------------------------------------------
# Critic models
# ------------------------------------------------------------------

class CriticAction(str, Enum):
    keep = "keep"
    revise = "revise"
    merge = "merge"
    remove = "remove"


class CriticFindingDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    finding_index: int = Field(..., ge=0)
    action: CriticAction
    merge_into_index: Optional[int] = Field(None, ge=0)
    strong_reason: bool = Field(default=False, description="True when the critic is confident the action is needed.")
    reason: str = Field(..., min_length=5, max_length=500)
    revised_title: Optional[str] = Field(None, min_length=3, max_length=200)
    revised_severity: Optional[Severity] = None
    revised_explanation: Optional[str] = Field(None, min_length=10, max_length=2000)
    revised_failure_scenario: Optional[str] = Field(None, min_length=10, max_length=2000)
    revised_suggestion: Optional[str] = Field(None, min_length=5, max_length=2000)


class CriticResult(BaseModel):
    model_config = ConfigDict(extra="forbid")

    decisions: list[CriticFindingDecision] = Field(default_factory=list)
    missing_findings: list[Finding] = Field(default_factory=list)
    reviewer_summary_update: Optional[str] = Field(None, max_length=2000)


# SQLAlchemy persistence models

class ReviewRun(Base):  # type: ignore[misc]
    __tablename__ = "review_runs"

    id = Column(Integer, primary_key=True)
    delivery_id = Column(String(64), index=True, nullable=False)
    installation_id = Column(Integer, nullable=False)
    repository_full_name = Column(String(255), nullable=False)
    pr_number = Column(Integer, nullable=False)
    pr_title = Column(String(500), nullable=True)
    status = Column(String(20), nullable=False, default="started")
    summary = Column(Text, nullable=True)
    risk_level = Column(String(20), nullable=True)
    suggested_tests = Column(Text, nullable=True)
    error_message = Column(Text, nullable=True)
    critic_decisions = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    completed_at = Column(DateTime, nullable=True)

    findings = relationship("FindingRecord", back_populates="review_run", cascade="all, delete-orphan")


class FindingRecord(Base):  # type: ignore[misc]
    __tablename__ = "finding_records"

    id = Column(Integer, primary_key=True)
    review_run_id = Column(Integer, ForeignKey("review_runs.id"), nullable=False)
    title = Column(String(255), nullable=False)
    category = Column(String(30), nullable=False)
    severity = Column(String(20), nullable=False)
    confidence = Column(Float, nullable=False)
    file_path = Column(String(500), nullable=False)
    line = Column(Integer, nullable=True)
    explanation = Column(Text, nullable=False)
    failure_scenario = Column(Text, nullable=False)
    suggestion = Column(Text, nullable=False)
    published_to_body = Column(Integer, default=0)

    review_run = relationship("ReviewRun", back_populates="findings")


class WebhookDelivery(Base):  # type: ignore[misc]
    __tablename__ = "webhook_deliveries"

    id = Column(Integer, primary_key=True)
    delivery_id = Column(String(64), index=True, unique=True, nullable=False)
    event_type = Column(String(64), nullable=False)
    action = Column(String(64), nullable=True)
    repository_full_name = Column(String(255), nullable=True)
    pr_number = Column(Integer, nullable=True)
    received_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    processed = Column(Integer, default=0)


# ------------------------------------------------------------------
# Waitlist
# ------------------------------------------------------------------

class WaitlistEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    email: str = Field(..., min_length=5, max_length=255)

    @field_validator("email")
    @classmethod
    def _validate_email(cls, v: str) -> str:
        import re
        if not re.match(r"^[^@\s]+@[^@\s]+\.[^@\s]+$", v):
            raise ValueError("Invalid email address")
        return v.lower().strip()


class WaitlistRecord(Base):  # type: ignore[misc]
    __tablename__ = "waitlist"

    id = Column(Integer, primary_key=True)
    email = Column(String(255), unique=True, nullable=False, index=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
