import json
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
import app.tools.llm
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


class FakeResponse:
    def __init__(self, status_code=200, payload=None, text=None):
        self.status_code = status_code
        self._payload = payload
        self.text = text if text is not None else (json.dumps(payload) if payload is not None else "")

    def json(self):
        if self._payload is None:
            raise ValueError("response body is not JSON")
        return self._payload


class FakeHTTPXClient:
    def __init__(self, response):
        self._response = response

    def get(self, *args, **kwargs):
        return self._response

    def close(self):
        pass


class WeatherClientErrorTest(unittest.TestCase):
    def _client(self, response):
        return tools.weather.WeatherClient()

    def _patch(self, response):
        return mock.patch.object(
            tools.weather.httpx, "Client", return_value=FakeHTTPXClient(response)
        )

    def test_valid_payload_builds_forecast(self):
        hours = [f"2026-08-20T{h:02d}:00" for h in range(24)]
        payload = {
            "current": {"time": "2026-08-20T10:00", "temperature_2m": 32.0},
            "hourly": {
                "time": hours * 2,
                "temperature_2m": [30.0] * 48,
                "precipitation": [0.0] * 48,
                "precipitation_probability": [0] * 48,
                "wind_speed_10m": [10.0] * 48,
                "weather_code": [3] * 48,
            },
        }
        with self._patch(FakeResponse(200, payload)):
            out = tools.weather.WeatherClient().fetch_forecast(13.08, 80.27)
        self.assertEqual(out["current"]["temperature"], 32.0)
        self.assertEqual(out["source"], "Open-Meteo")

    def test_http_error_raises_weather_api_error(self):
        with self._patch(FakeResponse(500, text="boom")), self.assertRaises(tools.weather.WeatherAPIError):
            tools.weather.WeatherClient().fetch_forecast(13.08, 80.27)

    def test_api_error_payload_raises_weather_api_error(self):
        payload = {"error": True, "reason": "Latitude must be in range [-90, 90]."}
        with self._patch(FakeResponse(200, payload)), self.assertRaises(tools.weather.WeatherAPIError):
            tools.weather.WeatherClient().fetch_forecast(999, 80.27)

    def test_non_json_body_raises_weather_api_error(self):
        with self._patch(FakeResponse(200, text="<html>gateway error</html>")), self.assertRaises(tools.weather.WeatherAPIError):
            tools.weather.WeatherClient().fetch_forecast(13.08, 80.27)

    def test_non_dict_json_raises_weather_api_error(self):
        with self._patch(FakeResponse(200, payload=["not", "a", "dict"])), self.assertRaises(tools.weather.WeatherAPIError):
            tools.weather.WeatherClient().fetch_forecast(13.08, 80.27)


class LLMClientTest(unittest.TestCase):
    def _make_settings(self, provider):
        from types import SimpleNamespace

        return SimpleNamespace(
            llm_provider=provider,
            llm_base_url="https://generativelanguage.googleapis.com/v1beta/openai",
            llm_api_key="test-key",
            llm_model="gemini-3.6-flash",
            llm_temperature=0.2,
            llm_timeout_seconds=5,
        )

    class FakeResponse:
        def __init__(self, status_code, text, payload=None):
            self.status_code = status_code
            self.text = text
            self._payload = payload

        def json(self):
            return self._payload

    def _run_chat(self, provider, responses, captures):
        calls = []

        class FakeClient:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def post(self, url, headers=None, json=None):
                calls.append(dict(json))
                captures["calls"] = calls
                return responses.pop(0)

        with mock.patch.object(tools.llm.httpx, "Client", return_value=FakeClient()), mock.patch.object(
            tools.llm, "get_settings", return_value=self._make_settings(provider)
        ):
            return tools.llm.LLMClient().chat([{"role": "user", "content": "hi"}])

    def test_google_provider_omits_temperature(self):
        captures = {}
        out = self._run_chat(
            "google",
            [self.FakeResponse(200, "ok", {"choices": [{"message": {"content": '{"summary":"x"}'}}]})],
            captures,
        )
        self.assertNotIn("temperature", captures["calls"][0])
        self.assertIn('{"summary":"x"}', out)

    def test_groq_provider_keeps_temperature(self):
        captures = {}
        self._run_chat(
            "groq",
            [self.FakeResponse(200, "ok", {"choices": [{"message": {"content": "ok"}}]})],
            captures,
        )
        self.assertEqual(captures["calls"][0]["temperature"], 0.2)

    def test_400_temperature_error_retries_without_temperature(self):
        captures = {}
        out = self._run_chat(
            "groq",
            [
                self.FakeResponse(400, "Invalid value for 'temperature': unsupported parameter"),
                self.FakeResponse(200, "ok", {"choices": [{"message": {"content": "ok"}}]}),
            ],
            captures,
        )
        self.assertIn("temperature", captures["calls"][0])
        self.assertNotIn("temperature", captures["calls"][1])
        self.assertEqual(out, "ok")

    def test_400_unrelated_error_raises_llm_error(self):
        captures = {}
        with self.assertRaises(tools.llm.LLMError):
            self._run_chat(
                "groq",
                [self.FakeResponse(400, "some other invalid argument")],
                captures,
            )
        self.assertEqual(len(captures["calls"]), 1)


class DailySummaryTest(unittest.TestCase):
    """Exercise run_daily_summary against an isolated temp database."""

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        engine = create_engine(
            f"sqlite:///{os.path.join(self.tmp, 'test.db')}",
            connect_args={"check_same_thread": False},
        )
        Base.metadata.create_all(engine)
        self.Session = sessionmaker(bind=engine, autoflush=False)
        self.patcher = mock.patch("app.database.SessionLocal", self.Session)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def _seed(self, raw_json, summary_json="{}"):
        with self.Session() as db:
            loc = Location(name="TestCity", lat=13.0, lon=80.0)
            db.add(loc)
            db.flush()
            run = orchestrator.models.AgentRun(location_id=loc.id, status="completed")
            db.add(run)
            db.flush()
            db.add(
                orchestrator.models.ForecastSnapshot(
                    location_id=loc.id, raw_json=raw_json, summary_json=summary_json
                )
            )
            db.add(
                orchestrator.models.AnalysisResult(
                    run_id=run.id, location_id=loc.id, summary="Warm and humid.", source="ai"
                )
            )
            db.commit()
            return run.id, loc.id

    def _run_summary(self, run_id, loc_id):
        fake_run = mock.Mock(id=run_id, location_id=loc_id)
        with mock.patch.object(orchestrator, "run_location", return_value=fake_run), mock.patch.object(
            tools.email,
            "send_email",
            return_value={"status": "sent", "reason": "ok", "to": "x@y.z"},
        ) as send:
            orchestrator.run_daily_summary("daily")
        return send

    def test_valid_snapshot_includes_now_line(self):
        payload = {
            "current": {
                "weather_text": "Overcast",
                "temperature": 32.0,
                "wind_speed": 18.0,
                "humidity": 80,
            },
            "summary": {"max_temp": {"value": 33.0}},
            "source": "Open-Meteo",
        }
        run_id, loc_id = self._seed(json.dumps(payload))
        send = self._run_summary(run_id, loc_id)
        self.assertTrue(send.called)
        self.assertIn("Now: Overcast", send.call_args.args[1])
        with self.Session() as db:
            log = db.query(orchestrator.models.AlertLog).filter_by(risk_type="daily_summary").one()
            self.assertEqual(log.run_id, run_id)
            self.assertEqual(log.location_id, loc_id)
            self.assertEqual(log.status, "sent")

    def test_error_message_string_raw_json_does_not_crash(self):
        run_id, loc_id = self._seed(json.dumps("Open-Meteo error 500: service unavailable"))
        send = self._run_summary(run_id, loc_id)
        self.assertTrue(send.called)
        self.assertNotIn("Now:", send.call_args.args[1])

    def test_empty_raw_json_does_not_crash(self):
        run_id, loc_id = self._seed("")
        send = self._run_summary(run_id, loc_id)
        self.assertTrue(send.called)
        self.assertNotIn("Now:", send.call_args.args[1])

    def test_string_current_value_does_not_crash(self):
        payload = {"current": "unexpected string payload", "summary": {}}
        run_id, loc_id = self._seed(json.dumps(payload))
        send = self._run_summary(run_id, loc_id)
        self.assertTrue(send.called)
        self.assertNotIn("Now:", send.call_args.args[1])

    def test_invalid_json_raw_json_does_not_crash(self):
        run_id, loc_id = self._seed("{not valid json")
        send = self._run_summary(run_id, loc_id)
        self.assertTrue(send.called)
        self.assertNotIn("Now:", send.call_args.args[1])


if __name__ == "__main__":
    unittest.main(verbosity=2)