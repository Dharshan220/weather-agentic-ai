from __future__ import annotations

import smtplib
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Dict, Optional

from ..config import get_settings


def build_decision_email(location_name: str, decision: Dict, model_label: str) -> Dict[str, str]:
    """Build the alert email per the AI Decision format."""
    risks = decision.get("risks", [])
    problem = decision.get("problem") or "No active weather risks detected."
    overall = decision.get("overall_severity", "LOW")

    evidence_lines = []
    for r in risks:
        evidence_lines.append(f"• {r.get('type', '').title()}: {r.get('evidence_text', '')}")
    if not evidence_lines:
        evidence_lines.append("• No significant rain, heat, or wind conditions expected.")

    decision_line = f"{overall} RISK" if overall else "LOW RISK"
    action = decision.get("action") or "Monitor conditions. No immediate action required."

    text_body = (
        "🧠 AI DECISION\n\n"
        f"Location: {location_name}\n\n"
        "Problem:\n"
        f"{problem}\n\n"
        "Evidence:\n"
        + "\n".join(evidence_lines) + "\n\n"
        "Decision:\n"
        f"{decision_line}\n\n"
        "Action:\n"
        f"{action}\n\n"
        "Recommendations:\n"
        + "\n".join(f"• {r}" for r in decision.get("recommendations", [])) + "\n\n"
        "—\n"
        f"[Analysis: AI · {model_label}]\n"
        "[Weather data: Open-Meteo]"
    )

    bullets_html = "".join(f"<li>{r.get('type', '').title()}: {r.get('evidence_text', '')}</li>" for r in risks) or "<li>No significant rain, heat, or wind conditions expected.</li>"
    recs_html = "".join(f"<li>{r}</li>" for r in decision.get("recommendations", [])) or "<li>None.</li>"
    html_body = f"""<html><body style="font-family:Arial,sans-serif;max-width:640px;margin:auto">
<h2>🧠 AI DECISION</h2>
<p><strong>Location:</strong> {location_name}</p>
<h3>Problem</h3>
<p>{problem}</p>
<h3>Evidence</h3>
<ul>{bullets_html}</ul>
<h3>Decision</h3>
<p style="font-size:1.2em;color:{'#d32f2f' if overall in ('HIGH','EXTREME') else '#f9a825'};font-weight:bold">{decision_line}</p>
<h3>Action</h3>
<p>{action}</p>
<h3>Recommendations</h3>
<ul>{recs_html}</ul>
<hr><p style="color:#777;font-size:12px">
Analysis: AI · {model_label}<br>Weather data: Open-Meteo
</p></body></html>"""

    subject = f"🌦️ Weather Alert: {overall} risk for {location_name}"
    return {"subject": subject, "text": text_body, "html": html_body}


def build_daily_summary_email(sections: list, model_label: str) -> Dict[str, str]:
    """Build a daily digest email covering one or more locations."""
    subject = "🌤️ Daily Weather Summary"
    text_lines = ["🌤️ DAILY WEATHER SUMMARY", "=" * 40, ""]
    html_blocks = ['<html><body style="font-family:Arial,sans-serif;max-width:680px;margin:auto">',
                   "<h2>🌤️ Daily Weather Summary</h2>"]
    for s in sections:
        name = s.get("location", "Unknown")
        text_lines += [f"📍 {name}", "-" * 30]
        html_blocks.append(f'<h3>📍 {name}</h3>')
        if s.get("current"):
            c = s["current"] if isinstance(s["current"], dict) else None
            if c:
                text_lines.append(f"Now: {c.get('weather_text')}, {c.get('temperature')}°C, wind {c.get('wind_speed')} km/h, humidity {c.get('humidity')}%")
                html_blocks.append(f'<p>Now: <b>{c.get("weather_text")}</b>, {c.get("temperature")}°C, wind {c.get("wind_speed")} km/h, humidity {c.get("humidity")}%</p>')
        if s.get("analysis"):
            text_lines += ["", "AI Analysis:", s["analysis"]]
            html_blocks.append(f'<p><em>AI analysis:</em> {s["analysis"]}</p>')
        if s.get("risks"):
            text_lines += ["", "Risks:"]
            html_blocks.append("<ul>")
            for r in s["risks"]:
                line = f"{r.get('risk_type','')} - {r.get('severity','')} ({r.get('source','')})"
                text_lines.append(f"• {line}")
                html_blocks.append(f'<li>{r.get("risk_type","")} — <b>{r.get("severity","")}</b> <span style="color:#777">({r.get("source","")})</span></li>')
            html_blocks.append("</ul>")
        else:
            text_lines.append("No active risks.")
            html_blocks.append("<p>No active risks.</p>")
        if s.get("recommendations"):
            text_lines += ["", "Recommendations:"]
            html_blocks.append("<ul>")
            for r in s["recommendations"]:
                text_lines.append(f"• {r.get('text','')}")
                html_blocks.append(f"<li>{r.get('text','')}</li>")
            html_blocks.append("</ul>")
        text_lines += [""]
    text_lines += ["—", f"[Analysis: AI · {model_label}]", "[Weather data: Open-Meteo]"]
    html_blocks.append(f'<hr><p style="color:#777;font-size:12px">Analysis: AI · {model_label}<br>Weather data: Open-Meteo</p>')
    html_blocks.append("</body></html>")
    return {"subject": subject, "text": "\n".join(text_lines), "html": "".join(html_blocks)}


def send_email(subject: str, text_body: str, html_body: str, to: Optional[str] = None) -> Dict:
    s = get_settings()
    to = to or s.alert_to
    if not s.smtp_enabled or not s.smtp_host or not to:
        return {
            "status": "logged",
            "reason": "SMTP not configured or disabled (demo mode) — email logged instead of sent.",
            "to": to or "",
        }

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = s.smtp_from
    msg["To"] = to
    msg.attach(MIMEText(text_body, "plain", "utf-8"))
    msg.attach(MIMEText(html_body, "html", "utf-8"))

    try:
        with smtplib.SMTP(s.smtp_host, s.smtp_port, timeout=30) as server:
            server.starttls()
            server.login(s.smtp_user, s.smtp_password)
            server.sendmail(s.smtp_from, [to], msg.as_string())
        return {"status": "sent", "reason": "Email sent via SMTP.", "to": to}
    except Exception as e:  # noqa: BLE001
        return {"status": "failed", "reason": f"SMTP error: {e}", "to": to}