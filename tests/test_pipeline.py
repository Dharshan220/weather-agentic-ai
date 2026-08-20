import os
import tempfile
import unittest
from unittest import mock

os.environ.setdefault("DB_PATH", os.path.join(tempfile.gettempdir(), "test_weather_agent.db"))
os.environ.setdefault("LLM_API_KEY", "test-key")
os.environ.setdefault("LLM_MODEL", "test-model")
os.environ.setdefault("SMTP_ENABLED", "false")
os.environ.setdefault("ALERT_THRESHOLD", "HIGH")

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app import agents, orchestrator, tools
import app.agents.analysis
import app.agents.risk_detection
import app.agents.recommendations
import app.tools.weather
import app.tools.email
from app.config import get_settings, SEVERITY_LEVELS
from app.models import Base, Location

ANALYSIS_RESP = {
    "summary": "Warm and humid with heavy rain expected in the evening.",
    "key_points": ["Heavy rain 4-7 PM", "Breezy afternoon"],
    "worst_periods": {"rain": "Heavy rainfall 4-7 PM", "heat": None, "wind": "Breezy afternoon"},
}

RISK_ALERT_RESP = {
    "risks": [{"type": "rain", "severity": "HIGH", "reason": "Heavy rainfall expected with high probability"}],
    "problem": "Heavy rainfall expected between 4 PM-7 PM.",
    "action": "Send weather alert email.",
}

RISK_MONITOR_RESP = {
    "risks": [{"type": "rain", "severity": "LOW", "reason": "Light rain only"}],
    "problem": "Light rain possible.",
    "action": "Monitor conditions. No immediate action required.",
}

REC_RESP = {
    "recommendations": [
        {"risk_type": "rain", "priority": "high", "text": "Carry an umbrella and avoid low-lying areas."}
    ]
}


class FakeLLM:
    def __init__(self, risk_resp=RISK_ALERT_RESP):
        self.risk_resp = risk_resp

    def chat_json(self, system, user):
        if "senior meteorology analyst" in system:
            return ANALYSIS_RESP
        if "risk assessment agent" in system:
            return self.risk_resp
        if "weather safety advisor" in system:
            return REC_RESP
        return {}


class FakeWeatherClient:
    def fetch_forecast(self, lat, lon):
        return {
            "current": {
                "time": "2026-08-19T10:00",
                "temperature": 32.0,
                "apparent_temperature": 38.0,
                "humidity": 80,
                "precipitation": 0.0,
                "weather_code": 3,
                "weather_text": "Overcast",
                "wind_speed": 18.0,
                "wind_direction": 220,
            },
            "summary": {
                "max_temp": {"value": 33.0, "time": "2026-08-19T14:00", "time_label": "2 PM"},
                "total_precip_next24h": 22.0,
                "max_precip_hour": {"value": 8.0, "time": "2026-08-19T17:00", "time_label": "5 PM"},
                "max_rain_probability": {"value": 84, "time": "2026-08-19T16:00", "time_label": "4 PM"},
                "max_wind": {"value": 31.0, "time": "2026-08-19T15:00", "time_label": "3 PM"},
                "rain_window": {"label": "4 PM-7 PM", "total_mm": 22.0, "peak_mm": 8.0, "peak_probability": 84},
                "heat_window": None,
                "wind_window": None,
            },
            "source": "Open-Meteo",
        }

    def close(self):
        pass


class WeatherToolTest(unittest.TestCase):
    def test_rule_severity_rain(self):
        summary = {
            "max_precip_hour": {"value": 22.0},
            "max_temp": {"value": 30.0},
            "max_wind": {"value": 20.0},
            "max_rain_probability": {"value": 84},
            "rain_window": {"label": "4 PM-7 PM"},
            "heat_window": None,
            "wind_window": None,
        }
        risks = agents.risk_detection.rule_risks(summary)
        rain = next(r for r in risks if r["type"] == "rain")
        self.assertEqual(rain["severity"], "HIGH")
        self.assertIn("22 mm", rain["evidence_text"])
        self.assertIn("84%", rain["evidence_text"])

    def test_rule_severity_heat(self):
        summary = {
            "max_precip_hour": {"value": 0.0},
            "max_temp": {"value": 41.0},
            "max_wind": {"value": 20.0},
            "max_rain_probability": {"value": 10},
            "rain_window": None, "heat_window": None, "wind_window": None,
        }
        risks = agents.risk_detection.rule_risks(summary)
        heat = next(r for r in risks if r["type"] == "heat")
        self.assertEqual(heat["severity"], "EXTREME")

    def test_rule_severity_wind(self):
        summary = {
            "max_precip_hour": {"value": 0.0},
            "max_temp": {"value": 25.0},
            "max_wind": {"value": 55.0},
            "max_rain_probability": {"value": 10},
            "rain_window": None, "heat_window": None, "wind_window": None,
        }
        risks = agents.risk_detection.rule_risks(summary)
        wind = next(r for r in risks if r["type"] == "wind")
        self.assertEqual(wind["severity"], "HIGH")

    def test_verify_downgrade_detected(self):
        rule = [{"type": "rain", "severity": "HIGH"}]
        llm = {"risks": [{"type": "rain", "severity": "LOW"}]}
        result = agents.risk_detection.verify(rule, llm)
        self.assertFalse(result["match"])
        self.assertIn("downgraded", result["detail"])

    def test_build_decision_alert(self):
        final = [{"type": "rain", "severity": "HIGH", "evidence_text": "x", "source": "rules"}]
        llm = {"problem": "Heavy rainfall expected between 4 PM-7 PM.", "action": "Send weather alert email."}
        decision = agents.risk_detection.build_decision(final, llm)
        self.assertEqual(decision["overall_severity"], "HIGH")
        self.assertTrue(decision["alert"])


class EmailToolTest(unittest.TestCase):
    def test_email_format(self):
        decision = {
            "overall_severity": "HIGH",
            "problem": "Heavy rainfall expected between 4 PM-7 PM.",
            "action": "Send weather alert email.",
            "risks": [
                {
                    "type": "rain",
                    "severity": "HIGH",
                    "evidence_text": "Expected precipitation: 22 mm; Rain probability: 84%",
                }
            ],
            "recommendations": ["Carry an umbrella and avoid low-lying areas."],
        }
        msg = tools.email.build_decision_email("Chennai", decision, "groq · llama-3.3-70b-versatile")
        self.assertIn("🧠 AI DECISION", msg["text"])
        self.assertIn("Heavy rainfall expected between 4 PM-7 PM.", msg["text"])
        self.assertIn("Expected precipitation: 22 mm", msg["text"])
        self.assertIn("Rain probability: 84%", msg["text"])
        self.assertIn("HIGH RISK", msg["text"])
        self.assertIn("Send weather alert email.", msg["text"])
        self.assertIn("AI · groq", msg["text"])
        self.assertIn("Open-Meteo", msg["text"])


class OrchestratorTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        engine = create_engine(
            f"sqlite:///{os.path.join(self.tmp, 'test.db')}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False)
        self.session = self.Session()
        self.location = Location(name="Chennai", lat=13.08, lon=80.27)
        self.session.add(self.location)
        self.session.commit()

    def tearDown(self):
        self.session.close()

    def test_full_pipeline_alert_sent(self):
        with (
            mock.patch.object(tools.weather, "WeatherClient", FakeWeatherClient),
            mock.patch.object(agents.analysis, "get_llm", return_value=FakeLLM()),
            mock.patch.object(agents.risk_detection, "get_llm", return_value=FakeLLM()),
            mock.patch.object(agents.recommendations, "get_llm", return_value=FakeLLM()),
            mock.patch.object(
                tools.email,
                "send_email",
                return_value={"status": "sent", "reason": "sent (test)", "to": "x@y.z"},
            ) as send,
        ):
            run = orchestrator.run_location(self.location, "manual", self.session)

        self.session.refresh(run)
        self.assertEqual(run.status, "completed")
        stages = {s.stage: s for s in run.stages}
        self.assertEqual(len(stages), 7)
        for s in run.stages:
            self.assertEqual(s.status, "completed")
        alerts = (
            self.session.query(orchestrator.models.AlertLog)
            .filter(orchestrator.models.AlertLog.run_id == run.id)
            .all()
        )
        self.assertEqual(len(alerts), 1)
        self.assertEqual(alerts[0].status, "sent")
        send.assert_called_once()

    def test_pipeline_monitor_no_email(self):
        with (
            mock.patch.object(tools.weather, "WeatherClient", FakeWeatherClient),
            mock.patch.object(agents.analysis, "get_llm", return_value=FakeLLM(RISK_MONITOR_RESP)),
            mock.patch.object(agents.risk_detection, "get_llm", return_value=FakeLLM(RISK_MONITOR_RESP)),
            mock.patch.object(agents.recommendations, "get_llm", return_value=FakeLLM(RISK_MONITOR_RESP)),
            mock.patch.object(tools.email, "send_email", return_value={"status": "sent", "reason": ""}),
        ):
            run = orchestrator.run_location(self.location, "manual", self.session)
        self.assertEqual(run.status, "completed")
        alert = (
            self.session.query(orchestrator.models.AlertLog)
            .filter(orchestrator.models.AlertLog.run_id == run.id)
            .one()
        )
        self.assertEqual(alert.status, "skipped")
        self.assertIn("threshold", alert.reason.lower())


if __name__ == "__main__":
    unittest.main(verbosity=2)