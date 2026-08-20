from __future__ import annotations

import json
from pathlib import Path
from typing import List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import desc

from ..config import get_settings
from ..database import SessionLocal
from .. import models
from ..models import (
    AgentRun,
    AlertLog,
    AnalysisResult,
    ForecastSnapshot,
    Location,
    RiskEvent,
)

STATIC_DIR = Path(__file__).parent / "static"


def _iso(dt) -> Optional[str]:
    return dt.isoformat() if dt else None


def _parse_json(text: str, default=None):
    try:
        return json.loads(text or "{}")
    except Exception:
        return default


def create_app() -> FastAPI:
    app = FastAPI(title="Weather Agent", version="1.0.0")

    @app.get("/")
    def index():
        return FileResponse(STATIC_DIR / "index.html")

    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    def _latest_snapshot(db, loc_id):
        return (
            db.query(ForecastSnapshot)
            .filter(ForecastSnapshot.location_id == loc_id)
            .order_by(desc(ForecastSnapshot.fetched_at))
            .first()
        )

    def _latest_run(db, loc_id):
        return (
            db.query(AgentRun)
            .filter(AgentRun.location_id == loc_id)
            .order_by(desc(AgentRun.started_at))
            .first()
        )

    def _latest_analysis(db, run_id):
        return (
            db.query(AnalysisResult)
            .filter(AnalysisResult.run_id == run_id)
            .order_by(desc(AnalysisResult.created_at))
            .first()
        )

    @app.get("/api/status")
    def status():
        db = SessionLocal()
        try:
            locations = db.query(Location).all()
            out = []
            for loc in locations:
                snap = _latest_snapshot(db, loc.id)
                run = _latest_run(db, loc.id)
                risks = (
                    db.query(RiskEvent)
                    .filter(RiskEvent.location_id == loc.id)
                    .order_by(desc(RiskEvent.created_at))
                    .limit(5)
                    .all()
                )
                analysis = _latest_analysis(db, run.id) if run else None
                alert = (
                    db.query(AlertLog)
                    .filter(AlertLog.location_id == loc.id)
                    .order_by(desc(AlertLog.created_at))
                    .first()
                )
                out.append(
                    {
                        "id": loc.id,
                        "name": loc.name,
                        "lat": loc.lat,
                        "lon": loc.lon,
                        "forecast": {
                            "current": _parse_json(snap.summary_json if snap else None, {})
                            .get("current")
                            if snap
                            else None,
                            "summary": _parse_json(snap.summary_json if snap else None, {}),
                            "fetched_at": _iso(snap.fetched_at) if snap else None,
                            "source": snap.source if snap else None,
                        },
                        "analysis": (
                            {
                                "summary": analysis.summary,
                                "key_points": _parse_json(analysis.key_points, []),
                                "source": analysis.source,
                                "created_at": _iso(analysis.created_at),
                            }
                            if analysis
                            else None
                        ),
                        "risks": [
                            {
                                "id": r.id,
                                "risk_type": r.risk_type,
                                "severity": r.severity,
                                "score": r.score,
                                "evidence": _parse_json(r.evidence, {}),
                                "source": r.source,
                                "created_at": _iso(r.created_at),
                            }
                            for r in risks
                        ],
                        "run": (
                            {
                                "id": run.id,
                                "status": run.status,
                                "triggered_by": run.triggered_by,
                                "started_at": _iso(run.started_at),
                                "finished_at": _iso(run.finished_at),
                                "error": run.error,
                            }
                            if run
                            else None
                        ),
                        "alert": (
                            {
                                "id": alert.id,
                                "severity": alert.severity,
                                "status": alert.status,
                                "reason": alert.reason,
                                "created_at": _iso(alert.created_at),
                            }
                            if alert
                            else None
                        ),
                    }
                )
            return {"locations": out}
        finally:
            db.close()

    @app.get("/api/runs")
    def runs(limit: int = 20):
        db = SessionLocal()
        try:
            rows = db.query(AgentRun).order_by(desc(AgentRun.started_at)).limit(limit).all()
            return {
                "runs": [
                    {
                        "id": r.id,
                        "location_id": r.location_id,
                        "status": r.status,
                        "triggered_by": r.triggered_by,
                        "started_at": _iso(r.started_at),
                        "finished_at": _iso(r.finished_at),
                        "error": r.error,
                    }
                    for r in rows
                ]
            }
        finally:
            db.close()

    @app.get("/api/runs/{run_id}")
    def run_detail(run_id: int):
        db = SessionLocal()
        try:
            run = db.get(AgentRun, run_id)
            if not run:
                raise HTTPException(status_code=404, detail="Run not found")
            loc = db.get(Location, run.location_id)
            stages = [
                {
                    "stage": s.stage,
                    "order": s.order,
                    "status": s.status,
                    "detail": s.detail,
                    "source": s.source,
                    "started_at": _iso(s.started_at),
                    "finished_at": _iso(s.finished_at),
                }
                for s in run.stages
            ]
            risks = [
                {
                    "risk_type": r.risk_type,
                    "severity": r.severity,
                    "score": r.score,
                    "evidence": _parse_json(r.evidence, {}),
                    "source": r.source,
                }
                for r in db.query(RiskEvent)
                .filter(RiskEvent.run_id == run_id)
                .all()
            ]
            recs = [
                {
                    "risk_type": r.risk_type,
                    "text": r.text,
                    "priority": r.priority,
                    "source": r.source,
                }
                for r in db.query(models.Recommendation)
                .filter(models.Recommendation.run_id == run_id)
                .all()
            ]
            analysis = _latest_analysis(db, run_id)
            alerts = [
                {
                    "risk_type": a.risk_type,
                    "severity": a.severity,
                    "status": a.status,
                    "reason": a.reason,
                    "created_at": _iso(a.created_at),
                }
                for a in db.query(AlertLog)
                .filter(AlertLog.run_id == run_id)
                .all()
            ]
            return {
                "run": {
                    "id": run.id,
                    "location": loc.name if loc else None,
                    "status": run.status,
                    "triggered_by": run.triggered_by,
                    "started_at": _iso(run.started_at),
                    "finished_at": _iso(run.finished_at),
                    "error": run.error,
                },
                "stages": stages,
                "risks": risks,
                "recommendations": recs,
                "analysis": (
                    {
                        "summary": analysis.summary,
                        "key_points": _parse_json(analysis.key_points, []),
                        "source": analysis.source,
                    }
                    if analysis
                    else None
                ),
                "alerts": alerts,
            }
        finally:
            db.close()

    @app.get("/api/alert-log")
    def alert_log(limit: int = 50):
        db = SessionLocal()
        try:
            rows = (
                db.query(AlertLog)
                .order_by(desc(AlertLog.created_at))
                .limit(limit)
                .all()
            )
            loc_names = {loc.id: loc.name for loc in db.query(Location).all()}
            return {
                "alerts": [
                    {
                        "id": a.id,
                        "location": loc_names.get(a.location_id),
                        "risk_type": a.risk_type,
                        "severity": a.severity,
                        "status": a.status,
                        "reason": a.reason,
                        "created_at": _iso(a.created_at),
                        "decision": _parse_json(a.decision_json, {}),
                    }
                    for a in rows
                ]
            }
        finally:
            db.close()

    @app.post("/api/run")
    def run_now(location: Optional[str] = None):
        from .. import orchestrator

        run_ids = orchestrator.run_all(triggered_by="manual", location_name=location)
        return {"triggered": True, "run_ids": run_ids}

    @app.get("/api/config")
    def config():
        cfg = get_settings()
        return {
            "llm_provider": cfg.llm_provider,
            "llm_model": cfg.llm_model,
            "llm_configured": cfg.has_llm_key,
            "schedule_interval_minutes": cfg.schedule_interval_minutes,
            "thresholds": {
                "rain_mm": cfg.rain_mm_threshold,
                "heat_c": cfg.heat_c_threshold,
                "wind_kmh": cfg.wind_kmh_threshold,
            },
            "alert_threshold": cfg.alert_threshold,
            "alert_cooldown_hours": cfg.alert_cooldown_hours,
            "smtp_enabled": cfg.smtp_enabled,
            "daily_summary_enabled": cfg.daily_summary_enabled,
            "daily_summary_times": cfg.daily_summary_time_list,
            "timezone": cfg.timezone,
            "locations": [
                {"name": l.name, "lat": l.lat, "lon": l.lon}
                for l in db_locations()
            ],
        }

    def db_locations():
        db = SessionLocal()
        try:
            return db.query(Location).all()
        finally:
            db.close()

    return app


app = create_app()