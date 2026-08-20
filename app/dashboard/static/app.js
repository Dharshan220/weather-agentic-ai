const $ = (sel) => document.querySelector(sel);

let runsCache = [];

async function fetchJSON(url, opts) {
  const res = await fetch(url, opts);
  if (!res.ok) throw new Error(`${res.status} ${res.statusText}`);
  return res.json();
}

function esc(s) {
  return String(s ?? '').replace(/[&<>"']/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;',
  }[c]));
}

function sevClass(sev) {
  return (sev || 'NONE').toUpperCase();
}

function sevBadge(sev, label) {
  const s = sevClass(sev);
  return `<span class="sev ${s} bg-${s}">${esc(label || s)}</span>`;
}

function timeAgo(iso) {
  if (!iso) return '—';
  const diff = (Date.now() - new Date(iso).getTime()) / 1000;
  if (diff < 60) return 'just now';
  if (diff < 3600) return `${Math.round(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.round(diff / 3600)}h ago`;
  return `${Math.round(diff / 86400)}d ago`;
}

function renderLocations(data) {
  const grid = $('#locations');
  if (!data.locations.length) {
    grid.innerHTML = '<div class="empty">No locations configured. Add them in .env (WEATHER_LOCATIONS).</div>';
    return;
  }
  grid.innerHTML = data.locations.map((loc) => {
    const cur = loc.forecast?.current || {};
    const sum = loc.forecast?.summary || {};
    const risks = (loc.risks || []);
    const run = loc.run;
    const analysis = loc.analysis;

    const riskHtml = risks.length
      ? risks.map((r) => `
        <div class="risk">
          <span>${esc(r.risk_type)} <span class="lbl">(${esc(r.source)})</span></span>
          ${sevBadge(r.severity)}
        </div>`).join('')
      : '<div class="empty" style="padding:10px">No active risks</div>';

    const curLine = cur.weather_text
      ? `<div class="weather-row"><span class="big">${esc(cur.temperature)}°C</span>
         <span>${esc(cur.weather_text)}</span>
         <span class="lbl">💧 ${cur.humidity ?? '—'}% · 🌬️ ${cur.wind_speed ?? '—'} km/h</span></div>
         <div class="weather-row"><span class="lbl">Feels like ${esc(cur.apparent_temperature)}°C · precip ${cur.precipitation ?? '—'} mm · fetched ${timeAgo(loc.forecast.fetched_at)}</span></div>`
      : '<div class="status-line">No forecast fetched yet.</div>';

    const runState = run
      ? `<div class="run-state">Last run: <b>${esc(run.status)}</b> · ${esc(run.triggered_by)} · ${timeAgo(run.finished_at)}</div>`
      : '<div class="status-line">No runs yet.</div>';

    const alertLine = loc.alert
      ? `<div class="run-state">Alert: ${sevBadge(loc.alert.severity)} <span class="lbl">${esc(loc.alert.status)}</span> · ${timeAgo(loc.alert.created_at)}</div>`
      : '';

    const analysisHtml = analysis
      ? `<div class="analysis-box"><span class="ai-tag">AI ANALYSIS · ${esc(analysis.source)}</span>
         <p>${esc(analysis.summary)}</p></div>`
      : '<div class="status-line">No AI analysis yet.</div>';

    return `
      <div class="card">
        <div>
          <h3>📍 ${esc(loc.name)}</h3>
          <span class="coords">${loc.lat}, ${loc.lon}</span>
        </div>
        ${curLine}
        ${analysisHtml}
        <div class="risk-list">${riskHtml}</div>
        ${runState}
        ${alertLine}
      </div>`;
  }).join('');
}

function renderTrace(runId) {
  const trace = $('#trace');
  if (!runId) {
    trace.innerHTML = '<div class="empty">Select a run to view its Agent Activity / Decision Trace.</div>';
    return;
  }
  fetchJSON(`/api/runs/${runId}`).then((data) => {
    const r = data.run;
    const header = `<div class="card" style="margin-bottom:12px">
      <b>Run #${r.id}</b> · ${esc(r.location)} · ${esc(r.status)} · ${esc(r.triggered_by)} ·
      ${timeAgo(r.started_at)} → ${timeAgo(r.finished_at)}
      ${r.error ? `<div class="run-state" style="color:var(--red)">Error: ${esc(r.error)}</div>` : ''}
    </div>`;

    const stages = data.stages.map((s) => `
      <div class="stage">
        <div class="ord">${s.order}</div>
        <div class="body">
          <div class="stage-name">${esc(s.stage)} <span class="status-pill status-${esc(s.status)}">${esc(s.status)}</span>
            <span class="meta">${s.source ? `· source: ${esc(s.source)}` : ''} · ${timeAgo(s.finished_at)}</span></div>
          <div class="detail">${esc(s.detail || '')}</div>
        </div>
      </div>`).join('');

    const risks = data.risks.length
      ? data.risks.map((r) => `<div class="risk"><span>${esc(r.risk_type)} (${esc(r.source)})</span>${sevBadge(r.severity)}</div>`).join('')
      : '<div class="empty" style="padding:10px">No risks detected</div>';

    const recs = data.recommendations.length
      ? data.recommendations.map((r) => `<div class="risk"><span>${esc(r.risk_type)} · ${esc(r.priority)}</span><span class="lbl">${esc(r.text)}</span></div>`).join('')
      : '<div class="empty" style="padding:10px">No recommendations</div>';

    const alerts = data.alerts.length
      ? data.alerts.map((a) => `<div class="risk"><span>${esc(a.risk_type)} · ${esc(a.status)}</span>${sevBadge(a.severity)}<span class="lbl">${esc(a.reason)}</span></div>`).join('')
      : '<div class="empty" style="padding:10px">No alerts in this run</div>';

    trace.innerHTML = header +
      `<div class="timeline">${stages}</div>
       <h3 style="margin:16px 0 8px">Final Risks</h3><div class="risk-list">${risks}</div>
       <h3 style="margin:16px 0 8px">Recommendations</h3><div class="risk-list">${recs}</div>
       <h3 style="margin:16px 0 8px">Alert</h3><div class="risk-list">${alerts}</div>`;
  }).catch((e) => {
    trace.innerHTML = `<div class="empty">Failed to load run: ${esc(e.message)}</div>`;
  });
}

function renderRuns(data) {
  runsCache = data.runs || [];
  const sel = $('#run-select');
  const prev = sel.value;
  sel.innerHTML = runsCache.length
    ? runsCache.map((r) => `<option value="${r.id}">#${r.id} · ${r.status} · ${r.triggered_by} · ${timeAgo(r.started_at)}</option>`).join('')
    : '<option value="">No runs yet</option>';
  if (prev && [...sel.options].some((o) => o.value === prev)) sel.value = prev;
  renderTrace(sel.value);
}

function renderAlerts(data) {
  const el = $('#alerts');
  const rows = data.alerts || [];
  if (!rows.length) {
    el.innerHTML = '<div class="empty">No alerts logged yet.</div>';
    return;
  }
  el.innerHTML = `<table><thead><tr>
    <th>Time</th><th>Location</th><th>Type</th><th>Severity</th><th>Status</th><th>Reason</th>
  </tr></thead><tbody>
    ${rows.map((a) => `<tr>
      <td>${timeAgo(a.created_at)}</td>
      <td>${esc(a.location)}</td>
      <td>${esc(a.risk_type)}</td>
      <td>${sevBadge(a.severity)}</td>
      <td>${esc(a.status)}</td>
      <td>${esc(a.reason || '')}</td>
    </tr>`).join('')}
  </tbody></table>`;
}

async function refresh() {
  try {
    const [status, runs, alerts, config] = await Promise.all([
      fetchJSON('/api/status'),
      fetchJSON('/api/runs'),
      fetchJSON('/api/alert-log'),
      fetchJSON('/api/config'),
    ]);
    renderLocations(status);
    renderRuns(runs);
    renderAlerts(alerts);
    const cfgEl = $('#config-badge');
    cfgEl.className = `badge ${config.llm_configured ? 'badge-ok' : 'badge-warn'}`;
    cfgEl.textContent = `${config.llm_provider} · ${config.llm_model}`;
  } catch (e) {
    console.error(e);
  }
}

async function runNow() {
  const btn = $('#run-btn');
  const status = $('#run-status');
  btn.disabled = true;
  status.className = 'run-status running';
  status.textContent = '▶ Agent pipeline running — fetching weather, analyzing with LLM, checking risks, generating recommendations…';
  status.classList.remove('hidden');
  try {
    const res = await fetchJSON('/api/run', { method: 'POST' });
    status.className = 'run-status done';
    status.textContent = `✓ Pipeline complete. Run IDs: ${(res.run_ids || []).join(', ')}. Refreshing dashboard…`;
    await refresh();
  } catch (e) {
    status.className = 'run-status error';
    status.textContent = `✗ Pipeline failed: ${e.message}`;
  } finally {
    btn.disabled = false;
    setTimeout(() => status.classList.add('hidden'), 8000);
  }
}

$('#run-btn').addEventListener('click', runNow);
$('#run-select').addEventListener('change', (e) => renderTrace(e.target.value));
refresh();
setInterval(refresh, 30000);