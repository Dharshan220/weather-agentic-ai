from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional

import httpx

OPEN_METEO_URL = "https://api.open-meteo.com/v1/forecast"


class WeatherAPIError(Exception):
    """Raised when the weather provider returns an error or an unexpected payload."""

WMO_CODES = {
    0: "Clear sky",
    1: "Mainly clear",
    2: "Partly cloudy",
    3: "Overcast",
    45: "Fog",
    48: "Depositing rime fog",
    51: "Light drizzle",
    53: "Moderate drizzle",
    55: "Dense drizzle",
    56: "Light freezing drizzle",
    57: "Dense freezing drizzle",
    61: "Slight rain",
    63: "Moderate rain",
    65: "Heavy rain",
    66: "Light freezing rain",
    67: "Heavy freezing rain",
    71: "Slight snow fall",
    73: "Moderate snow fall",
    75: "Heavy snow fall",
    77: "Snow grains",
    80: "Slight rain showers",
    81: "Moderate rain showers",
    82: "Violent rain showers",
    85: "Slight snow showers",
    86: "Heavy snow showers",
    95: "Thunderstorm",
    96: "Thunderstorm with slight hail",
    99: "Thunderstorm with heavy hail",
}


def weather_text(code: Optional[int]) -> str:
    return WMO_CODES.get(int(code or 0), "Unknown")


def format_hour(iso: str) -> str:
    """Convert an ISO timestamp to a compact hour label like '4 PM'."""
    try:
        h = int(iso[11:13])
        return f"{((h + 11) % 12) + 1} {'AM' if h < 12 else 'PM'}"
    except Exception:
        return iso


def format_window(start_iso: str, end_iso: str) -> str:
    return f"{format_hour(start_iso)}–{format_hour(end_iso)}"


def _find_windows(times: List[str], values: List[float], predicate) -> List[Dict]:
    windows = []
    start = None
    for t, v in zip(times, values):
        if predicate(v):
            if start is None:
                start = t
        else:
            if start is not None:
                windows.append({"start": start, "end": t})
                start = None
    if start is not None:
        windows.append({"start": start, "end": times[-1]})
    return windows


class WeatherClient:
    def __init__(self, timeout: float = 30.0):
        self._client = httpx.Client(timeout=timeout)

    def fetch_forecast(self, lat: float, lon: float) -> Dict:
        params = {
            "latitude": lat,
            "longitude": lon,
            "current": "temperature_2m,relative_humidity_2m,apparent_temperature,precipitation,weather_code,wind_speed_10m,wind_direction_10m",
            "hourly": "temperature_2m,precipitation,precipitation_probability,wind_speed_10m,weather_code",
            "forecast_days": 3,
            "timezone": "auto",
        }
        resp = self._client.get(OPEN_METEO_URL, params=params)
        if resp.status_code >= 400:
            raise WeatherAPIError(
                f"Open-Meteo error {resp.status_code}: {resp.text[:300]}"
            )
        try:
            data = resp.json()
        except ValueError as e:
            raise WeatherAPIError(
                f"Open-Meteo returned non-JSON response: {resp.text[:300]}"
            ) from e
        if not isinstance(data, dict):
            raise WeatherAPIError(
                f"Open-Meteo returned unexpected payload type: {type(data).__name__}"
            )
        if data.get("error"):
            reason = data.get("reason") or data.get("message") or repr(data)[:300]
            raise WeatherAPIError(f"Open-Meteo API error: {reason}")
        return self._build_payload(data)

    def _build_payload(self, data: Dict) -> Dict:
        current = data.get("current", {})
        hourly = data.get("hourly", {})
        times: List[str] = hourly.get("time", [])
        temps = hourly.get("temperature_2m", [])
        precip = hourly.get("precipitation", [])
        probs = hourly.get("precipitation_probability", [])
        winds = hourly.get("wind_speed_10m", [])

        def safe_max(values, times, key) -> Optional[Dict]:
            pairs = [(v, t) for v, t in zip(values, times) if v is not None]
            if not pairs:
                return None
            v, t = max(pairs, key=key)
            return {"value": round(v, 1), "time": t, "time_label": format_hour(t)}

        next24 = times[:24]
        max_temp = safe_max(temps[:24], next24, lambda p: p[0])
        max_precip_hour = safe_max(precip[:24], next24, lambda p: p[0])
        max_prob_hour = safe_max(probs[:24], next24, lambda p: p[0])
        max_wind = safe_max(winds[:24], next24, lambda p: p[0])

        total_precip = round(sum(p or 0 for p in precip[:24]), 1)

        rain_windows = _find_windows(times[:48], precip, lambda v: (v or 0) >= 1.0)
        rain_window = None
        if rain_windows:
            best = max(rain_windows, key=lambda w: sum(
                (p or 0) for p, t in zip(precip[:48], times[:48])
                if w["start"] <= t < w["end"]
            ))
            win_mm = round(sum(
                (p or 0) for p, t in zip(precip[:48], times[:48])
                if best["start"] <= t < best["end"]
            ), 1)
            peak_idx = precip[:48].index(max(precip[:48], default=0))
            rain_window = {
                "start": best["start"], "end": best["end"],
                "label": format_window(best["start"], best["end"]),
                "total_mm": win_mm,
                "peak_mm": round(precip[peak_idx] or 0, 1),
                "peak_probability": probs[peak_idx] if peak_idx < len(probs) else None,
            }

        heat_windows = _find_windows(times[:48], temps, lambda v: (v or 0) >= 30.0)
        heat_window = None
        if heat_windows:
            best = max(heat_windows, key=lambda w: max(
                (v or 0) for v, t in zip(temps[:48], times[:48]) if w["start"] <= t < w["end"]
            ))
            heat_window = {
                "start": best["start"], "end": best["end"],
                "label": format_window(best["start"], best["end"]),
                "max_temp": max((v or 0) for v, t in zip(temps[:48], times[:48]) if best["start"] <= t < best["end"]),
            }

        wind_windows = _find_windows(times[:48], winds, lambda v: (v or 0) >= 40.0)
        wind_window = None
        if wind_windows:
            best = max(wind_windows, key=lambda w: max(
                (v or 0) for v, t in zip(winds[:48], times[:48]) if w["start"] <= t < w["end"]
            ))
            wind_window = {
                "start": best["start"], "end": best["end"],
                "label": format_window(best["start"], best["end"]),
                "max_wind": max((v or 0) for v, t in zip(winds[:48], times[:48]) if best["start"] <= t < best["end"]),
            }

        summary = {
            "max_temp": max_temp,
            "total_precip_next24h": total_precip,
            "max_precip_hour": max_precip_hour,
            "max_rain_probability": max_prob_hour,
            "max_wind": max_wind,
            "rain_window": rain_window,
            "heat_window": heat_window,
            "wind_window": wind_window,
        }

        return {
            "current": {
                "time": current.get("time"),
                "temperature": current.get("temperature_2m"),
                "apparent_temperature": current.get("apparent_temperature"),
                "humidity": current.get("relative_humidity_2m"),
                "precipitation": current.get("precipitation"),
                "weather_code": current.get("weather_code"),
                "weather_text": weather_text(current.get("weather_code")),
                "wind_speed": current.get("wind_speed_10m"),
                "wind_direction": current.get("wind_direction_10m"),
            },
            "summary": summary,
            "source": "Open-Meteo",
        }

    def close(self) -> None:
        self._client.close()