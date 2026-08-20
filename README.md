# 🌤️ Autonomous AI Weather Monitoring Agent

A demo agent that fetches real weather data, analyzes the forecast with an LLM, detects rain/heat/wind risks, generates recommendations, and emails an alert when a configured risk threshold is reached.

## Architecture

```
Scheduler (APScheduler) ──▶ Orchestrator ──▶ Weather tool (Open-Meteo)
                               │  ▲
                               │  │  analysis agent (LLM: Groq/qwen3.6)
                               │  │  risk detection agent (rules + LLM)
                               │  │  verification (recheck once against rules)
                               │  │  recommendation agent (LLM)
                               │  ▼
                          Email tool (SMTP)  ──▶ SQLite (memory/state)
                               │
                          Dashboard (FastAPI + static UI)
```

Data sources are clearly separated everywhere:

- **Weather provider (Open-Meteo)** — raw forecast values and evidence (numbers, windows, probabilities).
- **AI (Groq · qwen/qwen3.6-27b)** — natural-language analysis, risk refinement, recommendations, and the decision.

## Features

- Real forecast data via [Open-Meteo](https://open-meteo.com) (no API key needed)
- LLM analysis, hybrid rule+AI risk detection, and a verification pass that cross-checks the AI decision against deterministic rules
- Email alerts formatted as `🧠 AI DECISION` (problem / evidence / decision / action) via SMTP, with a cooldown to prevent duplicate alerts
- Optional **daily summary email** (forecast digest for all monitored locations) sent automatically at a configured time
- **Run Agent Now** button + agent activity/decision trace (data collection → analysis → risk detection → verification → decision → recommendation → action) on the dashboard
- SQLite persistence of forecasts, analyses, risks, recommendations, and alert history

## Setup

1. Create a venv and install deps:

   ```powershell
   python -m venv .venv
   .\.venv\Scripts\pip install -r requirements.txt
   ```

2. Copy `.env.example` to `.env` and fill in your Groq API key (`LLM_API_KEY`) and, optionally, SMTP credentials for real email delivery. For Gmail use an app password.

3. Run:

   ```powershell
   .\.venv\Scripts\python run.py
   ```

4. Open the dashboard: http://127.0.0.1:8000

## Configuration (`.env`)

| Variable | Purpose |
| --- | --- |
| `LLM_PROVIDER` / `LLM_BASE_URL` / `LLM_API_KEY` / `LLM_MODEL` | Any OpenAI-compatible endpoint (Groq by default; Ollama, OpenAI, etc. work too) |
| `WEATHER_LOCATIONS` | JSON list of `{name, lat, lon}` to monitor |
| `SCHEDULE_INTERVAL_MINUTES` | How often the pipeline runs automatically |
| `RAIN_MM_THRESHOLD` / `HEAT_C_THRESHOLD` / `WIND_KMH_THRESHOLD` | Deterministic risk thresholds |
| `ALERT_THRESHOLD` | Severity that triggers an email (LOW/MEDIUM/HIGH/EXTREME) |
| `ALERT_COOLDOWN_HOURS` | Minimum hours between alerts per location+risk type |
| `SMTP_*` / `ALERT_TO` | SMTP credentials. Demo mode (`SMTP_ENABLED=false`) logs emails instead of sending. |
| `DAILY_SUMMARY_ENABLED` / `DAILY_SUMMARY_TIME` / `TIMEZONE` | Daily digest email at a set local time (e.g. `07:00` in `Asia/Kolkata`). |

## Tests

```powershell
.\.venv\Scripts\python -m unittest discover -s tests -v
```

## API

- `GET /api/status` — live status per location (provider weather + AI analysis + risks + last run)
- `GET /api/runs` / `GET /api/runs/{id}` — pipeline runs and decision trace
- `GET /api/alert-log` — email/alert history
- `POST /api/run` — trigger the full pipeline now (`?location=Chennai` optional)
- `GET /api/config` — active configuration