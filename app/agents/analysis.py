from __future__ import annotations

import json
from typing import Dict

from ..tools.llm import LLMError
from ..tools.llm import get_llm

ANALYSIS_SYSTEM = """You are a senior meteorology analyst. You are given structured weather forecast
data for a location and must translate it into a clear, concise, plain-English analysis.
Base every statement strictly on the provided data. Do not invent numbers.

Output ONLY one JSON object with keys: summary, key_points, worst_periods.
No markdown, no code fences, no schema placeholders, no explanations.

Example format (adapt values to the data you are given):
{"summary":"Warm and humid with heavy rain expected in the evening.","key_points":["Heavy rain expected 4 PM to 7 PM"],"worst_periods":{"rain":"Heavy rainfall between 4 PM and 7 PM","heat":null,"wind":"Breezy afternoon"}}"""


def _format_forecast(forecast: Dict) -> str:
    cur = forecast.get("current", {})
    s = forecast.get("summary", {})
    lines = [
        f"Location time: {cur.get('time')}",
        f"Current: {cur.get('temperature')}°C (feels like {cur.get('apparent_temperature')}°C), "
        f"humidity {cur.get('humidity')}%, {cur.get('weather_text')}, "
        f"wind {cur.get('wind_speed')} km/h, precipitation {cur.get('precipitation')} mm",
        f"Next 24h max temperature: {s.get('max_temp', {}).get('value')}°C at {s.get('max_temp', {}).get('time_label')}",
        f"Next 24h total precipitation: {s.get('total_precip_next24h')} mm",
        f"Peak hourly precipitation: {s.get('max_precip_hour', {}).get('value')} mm at {s.get('max_precip_hour', {}).get('time_label')}",
        f"Peak rain probability: {s.get('max_rain_probability', {}).get('value')}% at {s.get('max_rain_probability', {}).get('time_label')}",
        f"Peak wind: {s.get('max_wind', {}).get('value')} km/h at {s.get('max_wind', {}).get('time_label')}",
    ]
    for key, label in (("rain_window", "Rain"), ("heat_window", "Heat"), ("wind_window", "Wind")):
        w = s.get(key)
        if w:
            lines.append(f"{label} window: {w.get('label')} ({w})")
        else:
            lines.append(f"{label} window: none")
    return "\n".join(lines)


def rule_analysis(forecast: Dict) -> Dict:
    """Deterministic analysis (no LLM) built from provider data."""
    cur = forecast.get("current", {})
    s = forecast.get("summary", {})
    max_temp = s.get("max_temp", {})
    max_precip = s.get("max_precip_hour", {})
    prob = s.get("max_rain_probability", {})
    rain_window = s.get("rain_window")
    heat_window = s.get("heat_window")
    parts = []
    if cur.get("temperature") is not None:
        parts.append(f"Currently {cur.get('temperature')}°C ({cur.get('weather_text')})")
    if max_temp.get("value") is not None:
        parts.append(f"max {max_temp['value']}°C at {max_temp.get('time_label')}")
    if rain_window:
        parts.append(
            f"rain likely {rain_window.get('label')} "
            f"(~{max_precip.get('value')} mm/h peak, {prob.get('value')}% probability)"
        )
    elif max_precip.get("value"):
        parts.append(f"~{max_precip['value']} mm rain expected (peak {max_precip.get('time_label')})")
    if heat_window:
        parts.append(f"heat window {heat_window.get('label')}")
    return {
        "summary": ". ".join(parts) or "No significant weather events in the next 24 hours.",
        "key_points": parts,
        "worst_periods": {
            "rain": rain_window.get("label") if rain_window else None,
            "heat": heat_window.get("label") if heat_window else None,
            "wind": (s.get("wind_window") or {}).get("label"),
        },
    }


def analyze(forecast: Dict, location: Dict, use_llm: bool = True) -> Dict:
    """LLM analysis stage. Returns {summary, key_points, worst_periods}."""
    if not use_llm:
        return rule_analysis(forecast)
    user = (
        f"Location: {location['name']}\n"
        "Weather forecast data (source: Open-Meteo):\n"
        f"{_format_forecast(forecast)}"
    )
    try:
        return get_llm().chat_json(ANALYSIS_SYSTEM, user)
    except LLMError as e:
        raise LLMError(f"Analysis agent failed: {e}") from e