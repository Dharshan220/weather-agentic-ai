from __future__ import annotations

from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger

from . import orchestrator
from .config import get_settings

_scheduler: BackgroundScheduler | None = None


def _scheduled_job() -> None:
    orchestrator.run_all(triggered_by="scheduled")


def _daily_summary_job() -> None:
    orchestrator.run_daily_summary()


def start_scheduler() -> BackgroundScheduler:
    global _scheduler
    if _scheduler is not None:
        return _scheduler
    cfg = get_settings()
    _scheduler = BackgroundScheduler(timezone=cfg.timezone)
    _scheduler.add_job(
        _scheduled_job,
        trigger=IntervalTrigger(minutes=cfg.schedule_interval_minutes),
        id="weather_pipeline",
        replace_existing=True,
    )
    if cfg.daily_summary_enabled:
        for summary_time in cfg.daily_summary_time_list:
            hour, minute = (int(x) for x in summary_time.split(":"))
            _scheduler.add_job(
                _daily_summary_job,
                trigger=CronTrigger(hour=hour, minute=minute, timezone=cfg.timezone),
                id=f"daily_summary_{summary_time}",
                replace_existing=True,
            )
    _scheduler.start()
    return _scheduler


def stop_scheduler() -> None:
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None