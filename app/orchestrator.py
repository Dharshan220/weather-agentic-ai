from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Dict, List, Optional

from sqlalchemy.orm import Session

from . import agents, models, tools
from .config import get_settings

STAGES = [
    "data_collection",
    "analysis",
    "risk_detection",
    "verification",
    "decision",
    "recommendation",
    "action",
]


def _json(d: Dict) -> str:
    return json.dumps(d, default=str)


def _cooldown_active(db: Session, location_id: int, risk_type: str, now: datetime) -> bool:
    cfg = get_settings()
    window = now - timedelta(hours=cfg.alert_cooldown_hours)
    latest = (
        db.query(models.AlertLog)
        .filter(
            models.AlertLog.location_id == location_id,
            models.AlertLog.risk_type == risk_type,
            models.AlertLog.created_at >= window,
        )
        .order_by(models.AlertLog.created_at.desc())
        .first()
    )
    return latest is not None


def run_location(location: models.Location, triggered_by: str, db: Session) -> models.AgentRun:
    cfg = get_settings()
    use_llm = bool(location.use_llm)
    run = models.AgentRun(location_id=location.id, triggered_by=triggered_by, status="running")
    db.add(run)
    db.flush()

    def _stage(order: int, name: str, fn) -> Dict:
        log = models.StageLog(
            run_id=run.id, order=order, stage=name, status="running", source="orchestrator"
        )
        db.add(log)
        db.flush()
        try:
            result = fn()
            detail, source = result.get("detail", ""), result.get("source", "orchestrator")
            log.status = "completed"
            log.detail = detail if isinstance(detail, str) else json.dumps(detail, default=str)
            log.source = source
            log.finished_at = datetime.now()
            db.flush()
            return result
        except Exception as e:  # noqa: BLE001
            log.status = "failed"
            log.detail = str(e)
            log.finished_at = datetime.now()
            db.flush()
            raise

    try:
        forecast: Dict = {}

        def collect():
            nonlocal forecast
            client = tools.weather.WeatherClient()
            try:
                forecast = client.fetch_forecast(location.lat, location.lon)
            finally:
                client.close()
            snap = models.ForecastSnapshot(
                location_id=location.id,
                raw_json=_json(forecast),
                summary_json=_json(forecast["summary"]),
                source="provider",
            )
            db.add(snap)
            db.flush()
            cur = forecast["current"]
            detail = (
                f"{cur['weather_text']}, {cur['temperature']}°C, wind {cur['wind_speed']} km/h, "
                f"precip {cur['precipitation']} mm — next 24h peak rain "
                f"{forecast['summary']['max_precip_hour'].get('value')} mm, "
                f"max temp {forecast['summary']['max_temp'].get('value')}°C, "
                f"max wind {forecast['summary']['max_wind'].get('value')} km/h"
            )
            return {"detail": detail, "source": "Open-Meteo"}

        _stage(1, "data_collection", collect)

        analysis_out: Dict = {}

        def analyze():
            nonlocal analysis_out
            analysis_out = agents.analysis.analyze(forecast, {"name": location.name}, use_llm)
            db.add(
                models.AnalysisResult(
                    run_id=run.id,
                    location_id=location.id,
                    summary=analysis_out.get("summary", ""),
                    key_points=json.dumps(analysis_out.get("key_points", [])),
                    source="ai",
                )
            )
            db.flush()
            return {"detail": analysis_out.get("summary", ""), "source": "AI"}

        _stage(2, "analysis", analyze)

        rule_r: List[Dict] = []
        llm_out: Dict = {}
        final_risks: List[Dict] = []
        verification: Dict = {}

        def detect():
            nonlocal rule_r, llm_out
            rule_r = agents.risk_detection.rule_risks(forecast["summary"])
            llm_out = agents.risk_detection.llm_risks(forecast, rule_r, use_llm)
            detail = "; ".join(
                f"{r['type']}={r['severity']}" for r in llm_out.get("risks", [])
            ) or "no risks"
            return {"detail": detail, "source": "AI" if use_llm else "rules"}

        _stage(3, "risk_detection", detect)

        def verify_stage():
            nonlocal verification
            verification = agents.risk_detection.verify(rule_r, llm_out)
            return {
                "detail": verification["detail"],
                "source": "rules" if verification["match"] else "AI",
            }

        _stage(4, "verification", verify_stage)

        decision: Dict = {}

        def decide():
            nonlocal final_risks, decision
            final_risks = agents.risk_detection.combine_final(rule_r, llm_out)
            decision = agents.risk_detection.build_decision(final_risks, llm_out)
            for r in final_risks:
                db.add(
                    models.RiskEvent(
                        run_id=run.id,
                        location_id=location.id,
                        risk_type=r["type"],
                        severity=r["severity"],
                        score=r.get("score", 0.0),
                        evidence=_json(r.get("evidence", {})),
                        source=r.get("source", "ai"),
                    )
                )
            db.flush()
            return {
                "detail": {
                    "overall_severity": decision["overall_severity"],
                    "problem": decision["problem"],
                    "action": decision["action"],
                    "risks": [r["type"] for r in decision["risks"]],
                },
                "source": "AI",
            }

        _stage(5, "decision", decide)

        recommendations: List[Dict] = []

        def recommend_stage():
            nonlocal recommendations
            recommendations = agents.recommendations.recommend(
                decision, {"name": location.name}, use_llm
            )
            for rec in recommendations:
                db.add(
                    models.Recommendation(
                        run_id=run.id,
                        location_id=location.id,
                        risk_type=rec.get("risk_type", "general"),
                        text=rec.get("text", ""),
                        priority=rec.get("priority", "medium"),
                        source="ai",
                    )
                )
            db.flush()
            detail = "\n".join(f"{r.get('priority','')}: {r.get('text','')}" for r in recommendations)
            return {"detail": detail or "No active risks — no recommendations needed.", "source": "AI"}

        _stage(6, "recommendation", recommend_stage)

        def act():
            if not decision.get("alert"):
                db.add(
                    models.AlertLog(
                        run_id=run.id,
                        location_id=location.id,
                        risk_type="all",
                        severity=decision.get("overall_severity", "NONE"),
                        decision_json=_json(decision),
                        status="skipped",
                        reason="No risk at or above alert threshold.",
                    )
                )
                db.flush()
                return {"detail": "No alert threshold met — email skipped.", "source": "orchestrator"}

            active_types = [r["type"] for r in decision["risks"]]
            now = datetime.now()
            if any(_cooldown_active(db, location.id, t, now) for t in active_types):
                db.add(
                    models.AlertLog(
                        run_id=run.id,
                        location_id=location.id,
                        risk_type=",".join(active_types),
                        severity=decision.get("overall_severity", "HIGH"),
                        decision_json=_json(decision),
                        status="skipped",
                        reason=f"Alert cooldown active ({cfg.alert_cooldown_hours}h).",
                    )
                )
                db.flush()
                return {
                    "detail": f"Alert skipped — within cooldown window ({cfg.alert_cooldown_hours}h).",
                    "source": "orchestrator",
                }

            model_label = f"{cfg.llm_provider} · {cfg.llm_model}"
            msg = tools.email.build_decision_email(location.name, decision, model_label)
            result = tools.email.send_email(msg["subject"], msg["text"], msg["html"])
            db.add(
                models.AlertLog(
                    run_id=run.id,
                    location_id=location.id,
                    risk_type=",".join(active_types),
                    severity=decision.get("overall_severity", "HIGH"),
                    decision_json=_json(decision),
                    sent_to=result.get("to", ""),
                    status=result.get("status", "logged"),
                    reason=result.get("reason", ""),
                )
            )
            db.flush()
            return {"detail": result["reason"], "source": "SMTP"}

        _stage(7, "action", act)

        run.status = "completed"
    except Exception as e:  # noqa: BLE001
        run.status = "failed"
        run.error = str(e)
    finally:
        run.finished_at = datetime.now()
        db.commit()
    return run


def run_all(triggered_by: str = "manual", location_name: Optional[str] = None) -> List[int]:
    from .database import SessionLocal

    db = SessionLocal()
    ids = []
    try:
        query = db.query(models.Location)
        if location_name:
            query = query.filter(models.Location.name == location_name)
        for loc in query.all():
            run = run_location(loc, triggered_by, db)
            ids.append(run.id)
    finally:
        db.close()
    return ids


def run_daily_summary(triggered_by: str = "daily") -> None:
    """Refresh forecasts for all locations and email a daily digest."""
    from .database import SessionLocal

    cfg = get_settings()
    db = SessionLocal()
    try:
        locations = db.query(models.Location).all()
        sections = []
        run = None
        for loc in locations:
            run = run_location(loc, triggered_by, db)
            snap = (
                db.query(models.ForecastSnapshot)
                .filter(models.ForecastSnapshot.location_id == loc.id)
                .order_by(models.ForecastSnapshot.fetched_at.desc())
                .first()
            )
            analysis = (
                db.query(models.AnalysisResult)
                .filter(models.AnalysisResult.run_id == run.id)
                .first()
            )
            risks = (
                db.query(models.RiskEvent)
                .filter(models.RiskEvent.run_id == run.id)
                .all()
            )
            recs = (
                db.query(models.Recommendation)
                .filter(models.Recommendation.run_id == run.id)
                .all()
            )
            current = None
            if snap and snap.raw_json:
                try:
                    parsed = json.loads(snap.raw_json)
                except (TypeError, ValueError):
                    parsed = None
                if isinstance(parsed, dict):
                    cur = parsed.get("current")
                    current = cur if isinstance(cur, dict) else None
            sections.append(
                {
                    "location": loc.name,
                    "current": current,
                    "analysis": analysis.summary if analysis else None,
                    "risks": [
                        {
                            "risk_type": r.risk_type,
                            "severity": r.severity,
                            "source": r.source,
                        }
                        for r in risks
                    ],
                    "recommendations": [
                        {"text": r.text, "priority": r.priority} for r in recs
                    ],
                }
            )

        model_label = f"{cfg.llm_provider} · {cfg.llm_model}"
        msg = tools.email.build_daily_summary_email(sections, model_label)
        result = tools.email.send_email(msg["subject"], msg["text"], msg["html"])
        if run is not None:
            db.add(
                models.AlertLog(
                    run_id=run.id,
                    location_id=run.location_id,
                    risk_type="daily_summary",
                    severity="INFO",
                    decision_json=_json({"sections": len(sections)}),
                    sent_to=result.get("to", ""),
                    status=result.get("status", "logged"),
                    reason=result.get("reason", ""),
                )
            )
        db.commit()
        print(f"[daily_summary] {result['status']}: {result['reason']}")
    finally:
        db.close()
