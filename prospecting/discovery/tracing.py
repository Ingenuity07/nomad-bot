"""Durable, per-run observability traces for prospecting discovery.

Each discovery produces a machine-readable JSON trace, a human-readable flow
file, and a self-contained HTML viewer. Trace failures are deliberately
non-fatal: observability must never stop lead discovery.
"""

from __future__ import annotations

import html
import json
import logging
import os
import tempfile
import threading
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, Optional
from uuid import UUID

from django.conf import settings

logger = logging.getLogger(__name__)
_WRITE_LOCK = threading.RLock()


STAGES = {
    "user_input": "1. User input",
    "llm_input_interpretation": "2. LLM interprets input",
    "tool_search": "3. Tools search",
    "website_selection": "4. Eligible websites",
    "scraped_data": "5. Scraped data",
    "llm_scrape_interpretation": "6. LLM interprets scraped data",
    "completion": "Completion",
    "error": "Error",
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _trace_dir() -> Path:
    configured = getattr(
        settings,
        "DISCOVERY_TRACE_DIR",
        Path(settings.BASE_DIR) / "discovery_traces",
    )
    return Path(configured)


def _enabled() -> bool:
    return bool(getattr(settings, "DISCOVERY_TRACE_ENABLED", True))


def _safe_value(value: Any, *, string_limit: Optional[int] = None) -> Any:
    """Make trace data JSON-safe, bounded, and free of obvious secrets."""
    if string_limit is None:
        string_limit = int(getattr(settings, "DISCOVERY_TRACE_STRING_LIMIT", 100_000))

    if hasattr(value, "model_dump"):
        value = value.model_dump()
    elif hasattr(value, "dict") and not isinstance(value, dict):
        try:
            value = value.dict()
        except Exception:
            pass

    if isinstance(value, dict):
        safe = {}
        for key, item in value.items():
            key_text = str(key)
            key_lower = key_text.lower()
            if any(secret in key_lower for secret in (
                "api_key", "apikey", "token", "password", "secret",
                "authorization", "cookie", "passphrase",
            )):
                safe[key_text] = "[REDACTED]"
            else:
                safe[key_text] = _safe_value(item, string_limit=string_limit)
        return safe
    if isinstance(value, (list, tuple, set)):
        return [_safe_value(item, string_limit=string_limit) for item in value]
    if isinstance(value, (datetime, date, UUID, Path)):
        return str(value)
    if isinstance(value, bytes):
        value = value.decode("utf-8", errors="replace")
    if isinstance(value, str) and len(value) > string_limit:
        omitted = len(value) - string_limit
        return f"{value[:string_limit]}\n\n[TRACE TRUNCATED: {omitted} characters omitted]"
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)


def _result_count(output: Any) -> int:
    if not isinstance(output, dict):
        return 0
    for key in ("companies", "results", "pages", "emails", "phones", "linkedin_urls"):
        items = output.get(key)
        if isinstance(items, list):
            return len(items)
    return 0


def _quality_report(trace: Dict[str, Any]) -> Dict[str, Any]:
    events = trace.get("events", [])
    search_events = [event for event in events if event.get("stage") == "tool_search"]
    scrape_events = [event for event in events if event.get("stage") == "scraped_data"]
    selection_event = next(
        (event for event in reversed(events) if event.get("stage") == "website_selection"),
        None,
    )
    analysis_events = [
        event for event in events
        if event.get("stage") == "llm_scrape_interpretation"
    ]
    plan_events = [
        event for event in events
        if event.get("stage") == "llm_input_interpretation"
    ]
    completion = next(
        (event for event in reversed(events) if event.get("stage") == "completion"),
        None,
    )

    successful_searches = [event for event in search_events if event.get("status") == "success"]
    providers = sorted({
        str(event.get("metadata", {}).get("provider"))
        for event in successful_searches
        if event.get("metadata", {}).get("provider")
    })
    search_results = sum(
        int(event.get("metadata", {}).get("result_count", 0) or 0)
        for event in successful_searches
    )
    scraped_pages = sum(
        int(event.get("metadata", {}).get("page_count", 0) or 0)
        for event in scrape_events
    )
    scraped_chars = sum(
        int(event.get("metadata", {}).get("character_count", 0) or 0)
        for event in scrape_events
    )
    parsed_analyses = sum(
        1 for event in analysis_events
        if event.get("status") == "success" and event.get("metadata", {}).get("parsed", False)
    )
    failed_events = sum(1 for event in events if event.get("status") == "error")
    leads_found = 0
    if completion:
        leads_found = int((completion.get("output") or {}).get("leads_found", 0) or 0)
    selection_output = (selection_event or {}).get("output") or {}
    eligible_websites = int(selection_output.get("eligible_count", 0) or 0)
    selected_websites = int(selection_output.get("selected_count", 0) or 0)

    resolved_terms = []
    for event in plan_events:
        output = event.get("output") or {}
        terms = output.get("search_queries") or output.get("keywords") or output.get("resolved_search_terms")
        if isinstance(terms, list):
            resolved_terms.extend(str(term) for term in terms if term)

    def check(name: str, status: str, detail: str) -> Dict[str, str]:
        return {"name": name, "status": status, "detail": detail}

    checks = [
        check(
            "Search plan",
            "pass" if resolved_terms else "warn",
            f"{len(set(resolved_terms))} resolved search term(s)." if resolved_terms
            else "No resolved search terms have been recorded yet.",
        ),
        check(
            "Tool execution",
            "pass" if successful_searches else ("warn" if not search_events else "fail"),
            f"{len(successful_searches)}/{len(search_events)} search calls succeeded.",
        ),
        check(
            "Source yield",
            "pass" if search_results > 0 else ("warn" if not search_events else "fail"),
            f"{search_results} raw result(s) across {len(providers)} provider(s).",
        ),
        check(
            "Scrape coverage",
            "pass" if scraped_pages > 0 else ("warn" if leads_found == 0 else "fail"),
            f"{scraped_pages} page(s), {scraped_chars:,} characters captured.",
        ),
        check(
            "LLM analysis",
            "pass" if parsed_analyses > 0 else ("warn" if leads_found == 0 else "fail"),
            f"{parsed_analyses}/{len(analysis_events)} model interpretation(s) parsed.",
        ),
        check(
            "Runtime errors",
            "pass" if failed_events == 0 else "warn",
            "No recorded errors." if failed_events == 0 else f"{failed_events} event(s) need review.",
        ),
    ]
    score_values = {"pass": 100, "warn": 50, "fail": 0}
    score = round(sum(score_values[item["status"]] for item in checks) / len(checks))
    return {
        "score": score,
        "checks": checks,
        "metrics": {
            "search_calls": len(search_events),
            "successful_search_calls": len(successful_searches),
            "providers": providers,
            "raw_results": search_results,
            "eligible_websites": eligible_websites,
            "selected_websites": selected_websites,
            "scraped_pages": scraped_pages,
            "scraped_characters": scraped_chars,
            "parsed_llm_analyses": parsed_analyses,
            "failed_events": failed_events,
            "leads_found": leads_found,
        },
    }


class DiscoveryTraceRecorder:
    """Append events and continuously regenerate a per-discovery viewer."""

    def __init__(self, run_id: str):
        self.run_id = str(run_id)
        self.directory = _trace_dir()
        self.json_path = self.directory / f"{self.run_id}.json"
        self.html_path = self.directory / f"{self.run_id}.html"
        self.flow_path = self.directory / f"{self.run_id}-flow.md"

    def initialize(self, input_data: Optional[Dict[str, Any]] = None) -> None:
        if not _enabled():
            return
        with _WRITE_LOCK:
            trace = self._load() or {
                "schema_version": 1,
                "run_id": self.run_id,
                "status": "pending",
                "created_at": _utc_now(),
                "updated_at": _utc_now(),
                "input": {},
                "events": [],
            }
            if input_data:
                trace["input"] = _safe_value(input_data)
            if not any(event.get("stage") == "user_input" for event in trace["events"]):
                trace["events"].append({
                    "id": 1,
                    "timestamp": _utc_now(),
                    "stage": "user_input",
                    "stage_label": STAGES["user_input"],
                    "title": "Discovery request received",
                    "actor": "user",
                    "status": "success",
                    "input": _safe_value(input_data or {}),
                    "output": None,
                    "metadata": {"decision_source": "user"},
                })
            self._persist(trace)

    def event(
        self,
        stage: str,
        title: str,
        *,
        actor: str,
        input_data: Any = None,
        output_data: Any = None,
        status: str = "success",
        metadata: Optional[Dict[str, Any]] = None,
        duration_ms: Optional[int] = None,
    ) -> None:
        if not _enabled():
            return
        try:
            with _WRITE_LOCK:
                trace = self._load()
                if not trace:
                    self.initialize()
                    trace = self._load()
                events = trace.setdefault("events", [])
                event = {
                    "id": len(events) + 1,
                    "timestamp": _utc_now(),
                    "stage": stage,
                    "stage_label": STAGES.get(stage, stage.replace("_", " ").title()),
                    "title": title,
                    "actor": actor,
                    "status": status,
                    "input": _safe_value(input_data),
                    "output": _safe_value(output_data),
                    "metadata": _safe_value(metadata or {}),
                }
                if duration_ms is not None:
                    event["duration_ms"] = int(duration_ms)
                events.append(event)
                if stage == "completion":
                    trace["status"] = "completed"
                elif stage == "error":
                    trace["status"] = "failed"
                elif trace.get("status") == "pending":
                    trace["status"] = "running"
                self._persist(trace)
        except Exception:
            logger.exception("Failed to append discovery trace event for run %s", self.run_id)

    def record_tool_execution(
        self,
        tool_name: str,
        arguments: Dict[str, Any],
        result: Any,
    ) -> None:
        output = result.data if result.success else (
            result.error.model_dump() if result.error and hasattr(result.error, "model_dump")
            else str(result.error or "Unknown tool error")
        )
        if tool_name in ("search_companies", "search_web"):
            stage = "tool_search"
        else:
            stage = "scraped_data"
        metadata = {
            "tool_name": tool_name,
            "provider": result.provider,
            "result_count": _result_count(output),
            "decision_source": "workflow using LLM-derived search plan",
        }
        if tool_name == "crawl_website" and isinstance(output, dict):
            pages = output.get("pages") or []
            metadata.update({
                "page_count": len(pages),
                "character_count": sum(len(str(page.get("text", ""))) for page in pages if isinstance(page, dict)),
            })
        elif tool_name == "extract_contact_data" and isinstance(arguments, dict):
            metadata["character_count"] = len(str(arguments.get("text", "")))

        self.event(
            stage,
            f"{tool_name} → {result.provider or 'unknown provider'}",
            actor=f"tool:{tool_name}",
            input_data=arguments,
            output_data=output,
            status="success" if result.success else "error",
            duration_ms=result.duration_ms,
            metadata=metadata,
        )

    def _load(self) -> Optional[Dict[str, Any]]:
        if not self.json_path.exists():
            return None
        try:
            return json.loads(self.json_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.exception("Could not read discovery trace %s", self.json_path)
            return None

    def _persist(self, trace: Dict[str, Any]) -> None:
        self.directory.mkdir(parents=True, exist_ok=True)
        trace["updated_at"] = _utc_now()
        trace["quality"] = _quality_report(trace)
        self._atomic_write(self.json_path, json.dumps(trace, indent=2, ensure_ascii=False))
        self._atomic_write(self.flow_path, _render_flow_file(trace))
        self._atomic_write(self.html_path, _render_html(trace))

    @staticmethod
    def _atomic_write(path: Path, content: str) -> None:
        fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent, text=True)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
                handle.write(content)
            os.replace(temp_name, path)
        finally:
            if os.path.exists(temp_name):
                os.unlink(temp_name)


def load_discovery_trace(run_id: str) -> Optional[Dict[str, Any]]:
    return DiscoveryTraceRecorder(str(run_id))._load()


def discovery_trace_paths(run_id: str) -> Dict[str, Path]:
    recorder = DiscoveryTraceRecorder(str(run_id))
    return {
        "json": recorder.json_path,
        "html": recorder.html_path,
        "flow": recorder.flow_path,
    }


def _render_flow_file(trace: Dict[str, Any]) -> str:
    """Render a continuously updated, human-readable discovery audit file."""
    events = trace.get("events", [])
    quality = trace.get("quality", {})
    metrics = quality.get("metrics", {})
    selection = next(
        (event for event in reversed(events) if event.get("stage") == "website_selection"),
        {},
    )
    selection_output = selection.get("output") or {}
    websites = selection_output.get("websites") or []

    def json_block(value: Any) -> str:
        if value is None:
            return "_No data recorded._"
        return f"```json\n{json.dumps(value, indent=2, ensure_ascii=False)}\n```"

    def table_text(value: Any) -> str:
        return str(value or "—").replace("|", "\\|").replace("\n", " ")

    lines = [
        f"# Discovery data flow — {trace.get('run_id', '')}",
        "",
        f"- **Status:** {trace.get('status', 'pending')}",
        f"- **Created:** {trace.get('created_at', '—')}",
        f"- **Last updated:** {trace.get('updated_at', '—')}",
        "- **Scrape rule:** Every eligible website is listed; no more than 5 unique websites are scraped.",
        "",
        "## Discovery request",
        "",
        json_block(trace.get("input", {})),
        "",
        "## Flow summary",
        "",
        f"1. **Input interpreted** — {sum(1 for event in events if event.get('stage') == 'llm_input_interpretation')} planning event(s)",
        f"2. **Data fetched** — {metrics.get('raw_results', 0)} raw result(s)",
        f"3. **Websites evaluated** — {metrics.get('eligible_websites', 0)} eligible; {metrics.get('selected_websites', 0)} selected",
        f"4. **Data scraped** — {metrics.get('scraped_pages', 0)} page(s), {metrics.get('scraped_characters', 0)} character(s)",
        f"5. **LLM findings** — {metrics.get('parsed_llm_analyses', 0)} structured analysis result(s)",
        f"6. **Discoveries** — {metrics.get('leads_found', 0)} lead(s) found",
        "",
        "## All eligible websites",
        "",
    ]

    if websites:
        lines.extend([
            "| # | Company | Website | Scrape decision |",
            "|---:|---|---|---|",
        ])
        for index, website in enumerate(websites, start=1):
            decision = "Selected for scraping" if website.get("selected") else "Not scraped — five-website limit"
            lines.append(
                f"| {index} | {table_text(website.get('company_name'))} | "
                f"{table_text(website.get('url'))} | {decision} |"
            )
    else:
        lines.append("_Website candidates have not been recorded yet._")

    lines.extend(["", "## Step-by-step execution", ""])
    for index, event in enumerate(events, start=1):
        lines.extend([
            f"### Step {index}: {event.get('title', 'Untitled event')}",
            "",
            f"- **Time:** {event.get('timestamp', '—')}",
            f"- **Stage:** {event.get('stage_label') or event.get('stage', '—')}",
            f"- **Actor:** {event.get('actor', '—')}",
            f"- **Result:** {event.get('status', '—')}",
        ])
        if event.get("duration_ms") is not None:
            lines.append(f"- **Duration:** {event.get('duration_ms')} ms")
        lines.extend([
            "",
            "#### Data received / request",
            "",
            json_block(event.get("input")),
            "",
            "#### Data produced / findings",
            "",
            json_block(event.get("output")),
            "",
        ])

    return "\n".join(lines).rstrip() + "\n"


def _render_html(trace: Dict[str, Any]) -> str:
    payload = json.dumps(trace, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    title = html.escape(f"Discovery trace {trace.get('run_id', '')}")
    return _HTML_TEMPLATE.replace("__TITLE__", title).replace("__TRACE_JSON__", payload)


_HTML_TEMPLATE = r'''<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>__TITLE__</title>
<style>
:root { color-scheme: light dark; --bg:#f5f7fb; --panel:#fff; --text:#172033; --muted:#667085; --line:#d8dee9; --brand:#3659e3; --good:#087a55; --warn:#9a6700; --bad:#c73535; --soft:#eef2ff; }
@media (prefers-color-scheme:dark) { :root { --bg:#0f1420; --panel:#171d2a; --text:#edf1f7; --muted:#9aa5b6; --line:#303a4c; --brand:#8ba4ff; --good:#55d6a7; --warn:#f3c969; --bad:#ff8585; --soft:#202b4a; } }
* { box-sizing:border-box; } body { margin:0; background:var(--bg); color:var(--text); font:14px/1.5 ui-sans-serif,system-ui,-apple-system,"Segoe UI",sans-serif; }
main { max-width:1440px; margin:auto; padding:24px; } h1,h2,h3,p { margin-top:0; } h1 { font-size:24px; margin-bottom:4px; } h2 { font-size:17px; margin:26px 0 12px; } button,input,select { font:inherit; }
.muted { color:var(--muted); } .top { display:flex; gap:16px; justify-content:space-between; align-items:flex-start; flex-wrap:wrap; }
.status { padding:5px 10px; border-radius:999px; background:var(--soft); color:var(--brand); font-weight:650; text-transform:capitalize; }
.metrics { display:grid; grid-template-columns:repeat(auto-fit,minmax(155px,1fr)); gap:10px; margin-top:18px; }
.metric,.quality-item { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:12px; } .metric b { display:block; font-size:22px; }
.quality { display:grid; grid-template-columns:repeat(auto-fit,minmax(220px,1fr)); gap:10px; } .quality-item { display:grid; grid-template-columns:12px 1fr; gap:9px; } .dot { width:10px; height:10px; border-radius:50%; margin-top:5px; background:var(--muted); } .pass .dot { background:var(--good); } .warn .dot { background:var(--warn); } .fail .dot { background:var(--bad); }
.flow { display:grid; grid-template-columns:repeat(7,minmax(0,1fr)); gap:16px; align-items:stretch; } .flow-node { position:relative; min-width:0; background:var(--panel); border:1px solid var(--line); border-top:4px solid var(--brand); border-radius:10px; padding:14px; text-align:left; cursor:pointer; color:var(--text); } .flow-node:not(:last-child)::after { content:"→"; position:absolute; right:-15px; top:50%; color:var(--muted); font-size:18px; } .flow-node strong { display:block; margin-bottom:5px; } .flow-node span { color:var(--muted); }
.controls { display:flex; gap:10px; flex-wrap:wrap; margin-bottom:12px; } .controls input,.controls select { border:1px solid var(--line); background:var(--panel); color:var(--text); border-radius:8px; padding:9px 11px; } .controls input { flex:1; min-width:220px; }
.events { display:grid; gap:10px; } .event { display:grid; grid-template-columns:72px 14px minmax(0,1fr); gap:10px; } .event-time { color:var(--muted); font-size:12px; padding-top:14px; text-align:right; } .rail { position:relative; } .rail::before { content:""; position:absolute; left:6px; top:0; bottom:-11px; width:2px; background:var(--line); } .rail::after { content:""; position:absolute; left:2px; top:18px; width:10px; height:10px; border-radius:50%; background:var(--brand); }
.event-card { background:var(--panel); border:1px solid var(--line); border-radius:10px; padding:13px 15px; min-width:0; } .event-head { display:flex; gap:10px; justify-content:space-between; flex-wrap:wrap; } .event-title { font-weight:650; } .tag { color:var(--brand); font-size:12px; } .event.error .event-card { border-color:var(--bad); }
details { margin-top:9px; border-top:1px solid var(--line); padding-top:8px; } summary { cursor:pointer; color:var(--brand); } pre { max-height:440px; overflow:auto; white-space:pre-wrap; overflow-wrap:anywhere; background:var(--bg); padding:11px; border-radius:7px; color:var(--text); }
.empty { padding:28px; text-align:center; color:var(--muted); } .sr { position:absolute; width:1px; height:1px; padding:0; overflow:hidden; clip:rect(0,0,0,0); white-space:nowrap; border:0; }
@media(max-width:820px) { main { padding:15px; } .flow { grid-template-columns:1fr; gap:10px; } .flow-node:not(:last-child)::after { content:"↓"; right:auto; left:50%; top:auto; bottom:-18px; } .event { grid-template-columns:14px minmax(0,1fr); } .event-time { display:none; } }
</style>
</head>
<body>
<main>
  <div class="top"><div><h1>Discovery execution trace</h1><div id="subtitle" class="muted"></div></div><div id="status" class="status"></div></div>
  <div id="metrics" class="metrics"></div>
  <h2>Quality checks</h2><div id="quality" class="quality"></div>
  <h2>Flow map</h2><div id="flow" class="flow" aria-label="Discovery data flow"></div>
  <h2>Event inspector</h2>
  <div class="controls"><label class="sr" for="search">Search events</label><input id="search" type="search" placeholder="Search prompts, queries, providers, or scraped text"><label class="sr" for="stage">Filter stage</label><select id="stage"><option value="">All stages</option></select></div>
  <div id="events" class="events"></div>
</main>
<script id="trace-data" type="application/json">__TRACE_JSON__</script>
<script>
(() => {
  const trace = JSON.parse(document.getElementById('trace-data').textContent);
  const $ = id => document.getElementById(id);
  const stages = [
    ['user_input','User input'],['llm_input_interpretation','LLM plan'],['tool_search','Fetched data'],['website_selection','Eligible websites'],['scraped_data','Scraped data'],['llm_scrape_interpretation','LLM findings'],['completion','Discoveries']
  ];
  const pretty = value => JSON.stringify(value, null, 2);
  const eventsFor = stage => trace.events.filter(event => event.stage === stage);
  $('subtitle').textContent = `Run ${trace.run_id} · updated ${new Date(trace.updated_at).toLocaleString()}`;
  $('status').textContent = trace.status;
  const qm = trace.quality.metrics;
  const metrics = [['Quality', `${trace.quality.score}%`],['Events', trace.events.length],['Search results', qm.raw_results],['Scraped pages', qm.scraped_pages],['LLM analyses', qm.parsed_llm_analyses],['Errors', qm.failed_events]];
  metrics.forEach(([label,value]) => { const node=document.createElement('div'); node.className='metric'; const b=document.createElement('b'); b.textContent=value; const s=document.createElement('span'); s.className='muted'; s.textContent=label; node.append(b,s); $('metrics').append(node); });
  trace.quality.checks.forEach(check => { const node=document.createElement('div'); node.className=`quality-item ${check.status}`; const dot=document.createElement('span'); dot.className='dot'; const body=document.createElement('div'); const strong=document.createElement('strong'); strong.textContent=check.name; const detail=document.createElement('div'); detail.className='muted'; detail.textContent=check.detail; body.append(strong,detail); node.append(dot,body); $('quality').append(node); });
  stages.forEach(([key,label],index) => { const list=eventsFor(key); const button=document.createElement('button'); button.type='button'; button.className='flow-node'; button.dataset.stage=key; const strong=document.createElement('strong'); strong.textContent=`${index+1}. ${label}`; const span=document.createElement('span'); span.textContent=`${list.length} event${list.length===1?'':'s'}`; button.append(strong,span); button.addEventListener('click',()=>{ $('stage').value=key; render(); $('events').scrollIntoView({behavior:'smooth'}); }); $('flow').append(button); const option=document.createElement('option'); option.value=key; option.textContent=label; $('stage').append(option); });
  function addPayload(card,label,value) { if (value === null || value === undefined) return; const details=document.createElement('details'); const summary=document.createElement('summary'); summary.textContent=label; const pre=document.createElement('pre'); pre.textContent=pretty(value); details.append(summary,pre); card.append(details); }
  function render() {
    const query=$('search').value.trim().toLowerCase(), stage=$('stage').value; $('events').replaceChildren();
    const filtered=trace.events.filter(event => (!stage || event.stage===stage) && (!query || pretty(event).toLowerCase().includes(query)));
    filtered.forEach(event => { const row=document.createElement('article'); row.className=`event ${event.status}`; const time=document.createElement('div'); time.className='event-time'; time.textContent=new Date(event.timestamp).toLocaleTimeString([], {hour:'2-digit',minute:'2-digit',second:'2-digit'}); const rail=document.createElement('div'); rail.className='rail'; const card=document.createElement('div'); card.className='event-card'; const head=document.createElement('div'); head.className='event-head'; const title=document.createElement('div'); title.className='event-title'; title.textContent=event.title; const tag=document.createElement('div'); tag.className='tag'; tag.textContent=`${event.stage_label} · ${event.actor}${event.duration_ms!==undefined?` · ${event.duration_ms} ms`:''}`; head.append(title,tag); card.append(head); addPayload(card,'Input / prompt',event.input); addPayload(card,'Output / scraped data',event.output); addPayload(card,'Metadata',event.metadata); row.append(time,rail,card); $('events').append(row); });
    if (!filtered.length) { const empty=document.createElement('div'); empty.className='empty'; empty.textContent='No events match this filter.'; $('events').append(empty); }
  }
  $('search').addEventListener('input',render); $('stage').addEventListener('change',render); render();
  if (trace.status === 'pending' || trace.status === 'running') setTimeout(() => location.reload(), 5000);
})();
</script>
</body>
</html>'''
