from datetime import datetime
from typing import Optional

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


def now() -> datetime:
    return datetime.now()


class Location(Base):
    __tablename__ = "locations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100))
    lat: Mapped[float] = mapped_column(Float)
    lon: Mapped[float] = mapped_column(Float)
    use_llm: Mapped[bool] = mapped_column(default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class ForecastSnapshot(Base):
    __tablename__ = "forecast_snapshots"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    fetched_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    raw_json: Mapped[str] = mapped_column(Text)
    summary_json: Mapped[str] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(20), default="provider")


class AgentRun(Base):
    __tablename__ = "agent_runs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)
    status: Mapped[str] = mapped_column(String(20), default="running")
    triggered_by: Mapped[str] = mapped_column(String(20), default="scheduled")
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    stages: Mapped[list["StageLog"]] = relationship(
        back_populates="run", order_by="StageLog.order", cascade="all, delete-orphan"
    )


class StageLog(Base):
    __tablename__ = "stage_logs"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    order: Mapped[int] = mapped_column(Integer)
    stage: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    detail: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    source: Mapped[Optional[str]] = mapped_column(String(30), nullable=True)
    started_at: Mapped[datetime] = mapped_column(DateTime, default=now)
    finished_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    run: Mapped[AgentRun] = relationship(back_populates="stages")


class AnalysisResult(Base):
    __tablename__ = "analysis_results"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    summary: Mapped[str] = mapped_column(Text)
    key_points: Mapped[str] = mapped_column(Text, default="[]")
    source: Mapped[str] = mapped_column(String(30), default="ai")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    risk_type: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20))
    score: Mapped[float] = mapped_column(Float, default=0.0)
    evidence: Mapped[str] = mapped_column(Text, default="{}")
    source: Mapped[str] = mapped_column(String(30), default="ai")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class Recommendation(Base):
    __tablename__ = "recommendations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    risk_id: Mapped[Optional[int]] = mapped_column(ForeignKey("risk_events.id"), nullable=True)
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    risk_type: Mapped[str] = mapped_column(String(20))
    text: Mapped[str] = mapped_column(Text)
    priority: Mapped[str] = mapped_column(String(20), default="normal")
    source: Mapped[str] = mapped_column(String(30), default="ai")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)


class AlertLog(Base):
    __tablename__ = "alert_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    run_id: Mapped[int] = mapped_column(ForeignKey("agent_runs.id"))
    location_id: Mapped[int] = mapped_column(ForeignKey("locations.id"))
    risk_type: Mapped[str] = mapped_column(String(20))
    severity: Mapped[str] = mapped_column(String(20))
    decision_json: Mapped[str] = mapped_column(Text, default="{}")
    sent_to: Mapped[str] = mapped_column(String(200), default="")
    status: Mapped[str] = mapped_column(String(20), default="logged")
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=now)