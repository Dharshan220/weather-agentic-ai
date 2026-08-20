from __future__ import annotations

from typing import Dict, List

from ..tools.llm import LLMError
from ..tools.llm import get_llm

REC_SYSTEM = """You are a weather safety advisor. Given a weather risk assessment, produce concrete,
actionable recommendations for the affected people in the area. Be specific and practical.

Output ONLY one JSON object with a "recommendations" array with at least one item per active risk.
No markdown, no code fences, no schema placeholders, no explanations.

Example format (adapt values to the data you are given):
{"recommendations":[{"risk_type":"rain","priority":"high","text":"Carry an umbrella and avoid low-lying areas."},{"risk_type":"heat","priority":"medium","text":"Stay hydrated and avoid midday outdoor activity."}]}"""

FALLBACK = {
    "rain": {
        "high": "Carry an umbrella and avoid low-lying or flood-prone areas; delay travel during the heaviest rain window.",
        "medium": "Keep an umbrella handy and plan indoor alternatives during the rain window.",
        "low": "Expect light rain; keep rain gear accessible.",
    },
    "heat": {
        "high": "Stay hydrated, avoid outdoor activity during peak heat, and check on vulnerable individuals.",
        "medium": "Limit strenuous outdoor work and seek shade during the hottest hours.",
        "low": "Stay cool and hydrated during warm conditions.",
    },
    "wind": {
        "high": "Secure loose outdoor objects and avoid open areas during strong wind gusts.",
        "medium": "Be cautious outdoors and secure light furniture during gusty periods.",
        "low": "Expect breezy conditions; secure loose items.",
    },
    "general": {
        "high": "Stay informed with local updates and follow local authority guidance.",
        "medium": "Monitor local weather updates and plan accordingly.",
        "low": "No immediate action needed; monitor conditions.",
    },
}


def _fallback_recommendations(decision: Dict) -> List[Dict]:
    recs = []
    for r in decision.get("risks", []):
        rtype = r["type"] if r["type"] in FALLBACK else "general"
        priority = "high" if r["severity"] in ("HIGH", "EXTREME") else "medium" if r["severity"] == "MEDIUM" else "low"
        recs.append({"risk_type": rtype, "priority": priority, "text": FALLBACK[rtype][priority]})
    return recs


def recommend(decision: Dict, location: Dict, use_llm: bool = True) -> List[Dict]:
    if not decision.get("alert") and not decision.get("risks"):
        return []
    if not use_llm:
        return _fallback_recommendations(decision)
    risk_lines = []
    for r in decision.get("risks", []):
        risk_lines.append(
            f"- {r['type'].title()}: {r['severity']} - {r.get('evidence_text', '')} "
            f"({r.get('reason', '')})"
        )
    user = (
        f"Location: {location['name']}\n"
        f"Overall decision: {decision.get('overall_severity')} RISK\n"
        f"Problem: {decision.get('problem')}\n"
        "Active risks:\n"
        + ("\n".join(risk_lines) or "none")
    )
    try:
        out = get_llm().chat_json(REC_SYSTEM, user)
        recs = out.get("recommendations", [])
    except LLMError:
        recs = []
    if not recs:
        recs = _fallback_recommendations(decision)
    return recs