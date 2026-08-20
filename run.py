import uvicorn

from app.config import get_settings
from app.dashboard.api import app
from app.database import init_db
from app.scheduler import start_scheduler


def main() -> None:
    init_db()
    start_scheduler()
    cfg = get_settings()
    print(f"\nWeather Agent dashboard: http://{cfg.app_host}:{cfg.app_port}")
    print(f"LLM: {cfg.llm_provider} ({cfg.llm_model}) | Schedule: every {cfg.schedule_interval_minutes} min")
    print("Press Ctrl+C to stop.\n")
    uvicorn.run(app, host=cfg.app_host, port=cfg.app_port)


if __name__ == "__main__":
    main()