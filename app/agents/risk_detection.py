from __future__ import annotations

from typing import Dict, List, Optional

from ..config import get_settings, SEVERITY_LEVELS
from ..tools.llm import LLMError
from ..tools.llm import get_llm

RISK_TYPES = ["rain", "heat", "wind"]

RULE_TABLE = {
    "rain": [(1, "LOW"), (10, "MEDIUM"), (20, "HIGH"), (40, "EXTREME")],
    "heat": [(28, "LOW"), (32, "MEDIUM"), (35, "HIGH"), (38, "EXTREME")],
    "wind": [(30, "LOW"), (40, "MEDIUM"), (50, "HIGH"), (60, "EXTREME")],
}

RISK_THRESHOLD_VALUE = {"rain": "max_precip_hour", "heat": "max_temp", "wind": "max_wind"}


def _severity_for(value: float, rule_table) -> str:
    sev = "NONE"
    for threshold, label in rule_table:
        if value >= threshold:
            sev = label
    return sev


def _evidence_for(risk_type: str, summary: Dict) -> Dict:
    s = summary
    if risk_type == "rain":
        peak = s.get("max_precip_hour") or {}
        prob = s.get("max_rain_probability") or {}
        window = s.get("rain_window")
        return {
            "max_precip_mm": peak.get("value"),
            "precip_time": peak.get("time_label"),
            "rain_probability": prob.get("value"),
            "window": (window or {}).get("label") if window else None,
            "total_precip_24h": s.get("total_precip_next24h"),
        }
    if risk_type == "heat":
        peak = s.get("max_temp") or {}
        window = s.get("heat_window")
        return {
            "max_temp_c": peak.get("value"),
            "max_temp_time": peak.get("time_label"),
            "window": (window or {}).get("label") if window else None,
        }
    if risk_type == "wind":
        peak = s.get("max_wind") or {}
        window = s.get("wind_window")
        return {
            "max_wind_kmh": peak.get("value"),
            "max_wind_time": peak.get("time_label"),
            "window": (window or {}).get("label") if window else None,
        }
    return {}


def _num(v):
    if v is None:
        return None
    try:
        f = float(v)
        return int(f) if f.is_integer() else f
    except (TypeError, ValueError):
        return v


def _evidence_text(risk_type: str, evidence: Dict) -> str:
    parts = []
    if risk_type == "rain":
        if evidence.get("max_precip_mm") is not None:
            parts.append(f"Expected precipitation: {_num(evidence['max_precip_mm'])} mm")
        if evidence.get("rain_probability") is not None:
            parts.append(f"Rain probability: {_num(evidence['rain_probability'])}%")
        if evidence.get("total_precip_24h") is not None:
            parts.append(f"Total precip (24h): {_num(evidence['total_precip_24h'])} mm")
    elif risk_type == "heat":
        if evidence.get("max_temp_c") is not None:
            parts.append(f"Max temperature: {_num(evidence['max_temp_c'])}°C")
    elif risk_type == "wind":
        if evidence.get("max_wind_kmh") is not None:
            parts.append(f"Wind: {_num(evidence['max_wind_kmh'])} km/h")
    if evidence.get("window"):
        parts.append(f"Window: {evidence['window']}")
    return "; ".join(parts) or "No data"


def rule_risks(summary: Dict) -> List[Dict]:
    """Deterministic risk scoring from raw provider data."""
    cfg = get_settings()
    risks = []
    for risk_type in RISK_TYPES:
        key = RISK_THRESHOLD_VALUE[risk_type]
        peak = summary.get(key) or {}
        value = peak.get("value") or 0.0
        severity = _severity_for(float(value), RULE_TABLE[risk_type])
        score = min(100.0, (float(value) / (RULE_TABLE[risk_type][-1][0] or 1)) * 100.0)
        evidence = _evidence_for(risk_type, summary)
        if severity == "NONE":
            continue
        risks.append(
            {
                "type": risk_type,
                "severity": severity,
                "score": round(score, 1),
                "evidence": evidence,
                "evidence_text": _evidence_text(risk_type, evidence),
                "source": "rules",
            }
        )
    return risks


RISK_SYSTEM = """You are a weather risk assessment agent. Given a forecast summary and the
deterministic rule severities, confirm or refine each risk level using meteorological reasoning.
Never ignore hard data: if the rules flag HIGH or EXTREME, you may only downgrade if the data
clearly contradicts it, and you must explain why.

Output ONLY one JSON object. No markdown, no code fences, no schema placeholders, no explanations.

Example format (adapt values to the data you are given):
{"risks":[{"type":"rain","severity":"HIGH","reason":"Heavy rainfall expected with high probability"},{"type":"heat","severity":"LOW","reason":"Temperatures near threshold only"}],"problem":"Heavy rainfall expected between 4 PM and 7 PM.","action":"Send weather alert email."}"""


def llm_risks(forecast: Dict, rule_risks_list: List[Dict], use_llm: bool = True) -> Dict:
    """LLM refinement of risk levels + decision framing."""
    if not use_llm:
        return {"risks": []}
    cfg = get_settings()
    s = forecast.get("summary", {})
    rule_summary = ", ".join(
        f"{r['type']}={r['severity']}" for r in rule_risks_list
    ) or "no rules triggered"
    user = (
        f"Alert threshold: {cfg.alert_threshold} (trigger email if any risk is at or above this).\n"
        f"Rule severities: {rule_summary}\n"
        "Forecast highlights:\n"
        f"- Peak hourly precip: {s.get('max_precip_hour', {}).get('value')} mm ({s.get('max_precip_hour', {}).get('time_label')}) "
        f"with {s.get('max_rain_probability', {}).get('value')}% probability\n"
        f"- Rain window: {((s.get('rain_window') or {}).get('label')) if s.get('rain_window') else 'none'}\n"
        f"- Max temp: {s.get('max_temp', {}).get('value')}°C ({s.get('max_temp', {}).get('time_label')})\n"
        f"- Max wind: {s.get('max_wind', {}).get('value')} km/h ({s.get('max_wind', {}).get('time_label')})"
    )
    try:
        return get_llm().chat_json(RISK_SYSTEM, user)
    except LLMError as e:
        raise LLMError(f"Risk agent failed: {e}") from e


def verify(rule_risks_list: List[Dict], llm_out: Dict) -> Dict:
    """Cross-check the LLM decision against deterministic rules (recheck once)."""
    llm_by_type = {r.get("type"): r.get("severity", "NONE") for r in llm_out.get("risks", [])}
    mismatches = []
    for r in rule_risks_list:
        rule_sev = r["severity"]
        llm_sev = llm_by_type.get(r["type"], "NONE")
        if SEVERITY_LEVELS.index(llm_sev) < SEVERITY_LEVELS.index(rule_sev):
            mismatches.append(
                f"{r['type']}: rules={rule_sev}, LLM={llm_sev} (LLM downgraded below deterministic level)"
            )
    return {
        "match": len(mismatches) == 0,
        "detail": "LLM decision matches deterministic rules." if not mismatches else "; ".join(mismatches),
    }


def combine_final(rule_risks_list: List[Dict], llm_out: Dict) -> List[Dict]:
    """Final severity = the higher of rule severity and LLM severity (safety-first)."""
    llm_by_type = {r.get("type"): r.get("severity", "NONE") for r in llm_out.get("risks", [])}
    final = []
    types_seen = set()
    for r in rule_risks_list:
        types_seen.add(r["type"])
        llm_sev = llm_by_type.get(r["type"], "NONE")
        if SEVERITY_LEVELS.index(llm_sev) > SEVERITY_LEVELS.index(r["severity"]):
            r = dict(r)
            r["severity"] = llm_sev
            r["source"] = "ai"
        final.append(r)
    for llm_r in llm_out.get("risks", []):
        t = llm_r.get("type")
        if t in types_seen:
            continue
        if llm_r.get("severity", "NONE") == "NONE":
            continue
        final.append(
            {
                "type": t,
                "severity": llm_r["severity"],
                "score": 0.0,
                "evidence": {},
                "evidence_text": "",
                "reason": llm_r.get("reason", ""),
                "source": "ai",
            }
        )
    return final


def _problem_from_risks(final_risks: List[Dict]) -> str:
    ranked = sorted(
        final_risks,
        key=lambda r: SEVERITY_LEVELS.index(r["severity"]),
        reverse=True,
    )
    top = ranked[:2]
    if not top:
        return "No significant weather risks detected."
    sentences = []
    for r in top:
        label = r["type"].title()
        if r.get("evidence_text"):
            sentences.append(f"{r['severity'].title()} {label.lower()} risk: {r['evidence_text']}.")
        else:
            sentences.append(f"{r['severity'].title()} {label.lower()} risk expected.")
    return " ".join(sentences)


def build_decision(final_risks: List[Dict], llm_out: Dict) -> Dict:
    cfg = get_settings()
    thresh_idx = cfg.alert_threshold_index
    sev = "NONE"
    for r in final_risks:
        if SEVERITY_LEVELS.index(r["severity"]) > SEVERITY_LEVELS.index(sev):
            sev = r["severity"]
    alert = SEVERITY_LEVELS.index(sev) >= thresh_idx
    problem = _problem_from_risks(final_risks)
    return {
        "overall_severity": sev,
        "problem": problem,
        "action": "Send weather alert email." if alert else "Monitor conditions. No immediate action required.",
        "risks": [
            {
                "type": r["type"],
                "severity": r["severity"],
                "evidence_text": r.get("evidence_text", ""),
                "reason": r.get("reason", ""),
                "source": r.get("source", "ai"),
            }
            for r in final_risks
        ],
        "alert": alert,
    }