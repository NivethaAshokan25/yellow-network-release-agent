"""
Streamlit UI for The Yellow Network – Jira Release Evidence Agent.

Provides:
  - Import Dataset: import dataset.json into Jira (with dry-run option)
  - Run Assessment: trigger AI assessment and display results
"""
from __future__ import annotations

import json
import logging
import os
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock

import streamlit as st
from dotenv import load_dotenv

# Add parent to path for imports
sys.path.insert(0, str(Path(__file__).parent.parent))
load_dotenv()

logger = logging.getLogger(__name__)

# ── Page config ───────────────────────────────────────────────────────────────

st.set_page_config(
    page_title="TYN Release Evidence Agent",
    page_icon="🟡",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Styling ───────────────────────────────────────────────────────────────────

st.markdown(
    """
<style>
/* Fonts */
@import url('https://fonts.googleapis.com/css2?family=Inter:wght@300;400;500;600;700&display=swap');

html, body, [class*="css"] {
    font-family: 'Inter', sans-serif;
}

/* Dark gradient background */
.stApp {
    background: linear-gradient(135deg, #0d1117 0%, #161b22 50%, #0d1117 100%);
}

/* Main title */
.main-title {
    font-size: 2.2rem;
    font-weight: 700;
    background: linear-gradient(90deg, #f9d342, #ffa500, #f9d342);
    -webkit-background-clip: text;
    -webkit-text-fill-color: transparent;
    background-clip: text;
    text-align: center;
    padding: 1rem 0 0.5rem 0;
}

.subtitle {
    text-align: center;
    color: #8b949e;
    font-size: 1rem;
    margin-bottom: 2rem;
}

/* Card-style panels */
.verdict-card {
    border-radius: 12px;
    padding: 1.5rem;
    margin: 1rem 0;
    border: 1px solid rgba(255,255,255,0.1);
}

.verdict-ready { background: rgba(35, 134, 54, 0.15); border-color: #238636; }
.verdict-blocked { background: rgba(218, 54, 51, 0.15); border-color: #da3633; }
.verdict-needs-review { background: rgba(187, 128, 9, 0.15); border-color: #bb8009; }

/* Metric boxes */
.metric-box {
    background: rgba(255,255,255,0.05);
    border-radius: 8px;
    padding: 1rem;
    text-align: center;
    border: 1px solid rgba(255,255,255,0.08);
}

/* Requirement rows */
.req-pass { color: #3fb950; }
.req-fail { color: #f85149; }
.req-insufficient { color: #d29922; }

/* Sidebar */
.sidebar-label {
    font-size: 0.75rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: #8b949e;
    margin-bottom: 0.25rem;
}

/* Button overrides */
.stButton > button {
    border-radius: 8px !important;
    font-weight: 600 !important;
    transition: all 0.2s !important;
}

.stButton > button:hover {
    transform: translateY(-1px) !important;
    box-shadow: 0 4px 12px rgba(249, 211, 66, 0.3) !important;
}
</style>
""",
    unsafe_allow_html=True,
)

# ── Header ────────────────────────────────────────────────────────────────────

st.markdown('<div class="main-title">🟡 The Yellow Network</div>', unsafe_allow_html=True)
st.markdown(
    '<div class="subtitle">Jira Release Evidence Agent — Automated Release Readiness Assessment</div>',
    unsafe_allow_html=True,
)

# ── Sidebar: Configuration ────────────────────────────────────────────────────

with st.sidebar:
    st.markdown("## ⚙️ Configuration")
    st.markdown('<div class="sidebar-label">Jira Settings</div>', unsafe_allow_html=True)

    jira_project = st.text_input(
        "Project Key",
        value=os.environ.get("JIRA_PROJECT_KEY", "TYN"),
        help="Jira project key (e.g. TYN)",
        key="jira_project",
    )
    dataset_label = st.text_input(
        "Dataset Label",
        value="tyn-release-assessment-v2",
        help="Label applied to all imported issues",
        key="dataset_label",
    )
    target_release = st.text_input(
        "Target Release",
        value="R2.4",
        help="Release version to assess",
        key="target_release",
    )

    st.divider()
    st.markdown('<div class="sidebar-label">Advanced</div>', unsafe_allow_html=True)

    target_commit = st.text_input("Target Commit", value="b7a91f2", key="target_commit")
    target_service = st.text_input("Target Service", value="atlas-assistant", key="target_service")
    target_env = st.selectbox("Environment", ["production", "staging"], key="target_env")
    target_prompt = st.text_input("Prompt Version", value="p7", key="target_prompt")

    st.divider()
    st.markdown('<div class="sidebar-label">AI Model</div>', unsafe_allow_html=True)

    llm_provider = st.selectbox(
        "Model Provider",
        ["gemini", "ollama"],
        index=0 if os.environ.get("LLM_PROVIDER", "gemini").lower() == "gemini" else 1,
        key="llm_provider",
    )
    if llm_provider == "gemini":
        llm_model = st.text_input(
            "Gemini Model",
            value=os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite"),
            key="gemini_model",
        )
        if not os.environ.get("GEMINI_API_KEY"):
            st.warning("Set GEMINI_API_KEY in .env to use Gemini. Do not paste it in the UI.")
    else:
        llm_model = st.text_input(
            "Ollama Model",
            value=os.environ.get("OLLAMA_MODEL", "qwen3:4b"),
            key="ollama_model",
        )
        ollama_url = st.text_input(
            "Ollama URL",
            value=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
            key="ollama_url",
        )
    if llm_provider == "gemini":
        ollama_url = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")

    st.divider()
    st.markdown('<div class="sidebar-label">Dataset Path</div>', unsafe_allow_html=True)
    dataset_path = st.text_input(
        "dataset.json path",
        value=os.environ.get("DATASET_PATH", "inputs/dataset.json"),
        key="dataset_path",
    )
    db_path = st.text_input("DB path", value=os.environ.get("DB_PATH", "db/state.db"), key="db_path")
    reports_dir = st.text_input(
        "Reports dir", value=os.environ.get("REPORTS_DIR", "reports"), key="reports_dir"
    )

# ── Credential check helper ───────────────────────────────────────────────────

def _check_jira_credentials() -> tuple[bool, str]:
    base_url = os.environ.get("JIRA_BASE_URL", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")
    if not all([base_url, email, token]):
        return False, "JIRA_BASE_URL, JIRA_EMAIL, JIRA_API_TOKEN must be set in .env"
    return True, ""


def _get_jira_client():
    from jira.client import JiraClient

    return JiraClient(
        os.environ["JIRA_BASE_URL"],
        os.environ["JIRA_EMAIL"],
        os.environ["JIRA_API_TOKEN"],
    )


# ── Main tabs ─────────────────────────────────────────────────────────────────

tab_import, tab_assess, tab_reports, tab_about = st.tabs(
    ["📥 Import Dataset", "🤖 Run Assessment", "📊 Reports", "ℹ️ About"]
)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 1: Import
# ════════════════════════════════════════════════════════════════════════════════

with tab_import:
    st.markdown("### 📥 Import Dataset")
    st.markdown(
        "Import all records from `dataset.json` into Jira. "
        "The import is **idempotent** — re-running skips already-imported records."
    )

    col1, col2, col3 = st.columns(3)
    with col1:
        dry_run = st.checkbox("🔍 Dry-run (no Jira writes)", value=True, key="dry_run")
    with col2:
        force_reimport = st.checkbox("⚡ Force re-import", value=False, key="force_reimport")
    with col3:
        validate_after = st.checkbox("✅ Validate after import", value=False, key="validate_after")

    if force_reimport:
        st.warning(
            "Force re-import clears local tracking but does not delete Jira issues; "
            "use it only when duplicates are acceptable."
        )

    if st.button("🚀 Start Import", type="primary", key="btn_import"):
        from importer.loader import load_dataset
        from importer.jira_importer import run_import

        with st.spinner("Loading dataset..."):
            try:
                ds = load_dataset(dataset_path)
                st.success(
                    f"Dataset loaded: **{len(ds.records)} records** "
                    f"({len(ds.requirements)} requirements, 1 manifest)"
                )
            except FileNotFoundError as e:
                st.error(f"Dataset not found: {e}")
                st.stop()
            except Exception as e:
                st.error(f"Dataset error: {e}")
                st.stop()

        if not dry_run:
            creds_ok, creds_msg = _check_jira_credentials()
            if not creds_ok:
                st.error(f"❌ Jira credentials missing: {creds_msg}")
                st.stop()

        with st.spinner("Importing into Jira..."):
            if dry_run:
                mock_client = MagicMock()
                mock_client.get_project.return_value = {"key": jira_project}
                client = mock_client
            else:
                client = _get_jira_client()

            progress_bar = st.progress(0, text="Preparing import...")
            status_placeholder = st.empty()

            def update_import_progress(phase, completed, total, detail):
                phase_start, phase_weight = (0.0, 0.78) if phase == "records" else (0.78, 0.22)
                fraction = completed / total if total else 1.0
                progress_bar.progress(
                    min(1.0, phase_start + phase_weight * fraction),
                    text=f"{phase.title()}: {completed}/{total}",
                )
                status_placeholder.caption(detail)

            validation_report = None
            try:
                result = run_import(
                    dataset=ds,
                    client=client,
                    project_key=jira_project,
                    project_name=os.environ.get("JIRA_PROJECT_NAME", "Yellow Network"),
                    dry_run=dry_run,
                    force=force_reimport,
                    db_path=db_path,
                    progress_callback=update_import_progress,
                )
                if validate_after and not dry_run:
                    from importer.validator import validate_import

                    validation_report = validate_import(
                        client,
                        dataset=ds,
                        project_key=jira_project,
                        db_path=db_path,
                        progress_callback=lambda done, total, detail: (
                            progress_bar.progress(
                                0.78 + 0.22 * (done / total if total else 1.0),
                                text=f"Validating: {done}/{total}",
                            ),
                            status_placeholder.caption(detail),
                        ),
                    )
            except Exception as e:
                st.error(f"Import failed: {e}")
                st.stop()
            finally:
                if not dry_run:
                    client.close()

        progress_bar.progress(1.0, text="Import complete")
        status_placeholder.empty()
        col1, col2, col3, col4, col5, col6 = st.columns(6)
        with col1:
            st.metric("✅ Created", len(result.created))
        with col2:
            st.metric("⏭️ Skipped", len(result.skipped))
        with col3:
            st.metric("❌ Failed", len(result.failed))
        with col4:
            st.metric("💬 Comment Errors", len(result.comment_errors))
        with col5:
            st.metric("🔗 Link Errors", len(result.link_errors))
        with col6:
            st.metric("🔄 Status Errors", len(result.status_errors))

        if dry_run:
            st.info("🔍 Dry-run complete — no Jira writes performed.")
        elif (
            result.failed
            or result.comment_errors
            or result.status_errors
            or result.link_errors
        ):
            st.error(
                "Import finished with errors. Review the counts and details before using "
                "this project for assessment."
            )
        else:
            st.success(
                f"✅ Import complete: {len(result.created)} created, "
                f"{len(result.skipped)} skipped."
            )

        if validation_report:
            (st.success if validation_report.passed else st.error)(
                validation_report.summary()
            )

        if result.failed:
            with st.expander("❌ Failed records"):
                for src, err in result.failed:
                    st.text(f"{src}: {err}")

        if result.comment_errors:
            with st.expander("💬 Comment errors"):
                for src, jira_key, err in result.comment_errors:
                    st.text(f"{src} ({jira_key}): {err}")

        if result.status_errors:
            with st.expander("🔄 Status update errors"):
                for src, jira_key, err in result.status_errors:
                    st.text(f"{src} ({jira_key}): {err}")

        if result.link_errors:
            with st.expander("⚠️ Link errors"):
                for err in result.link_errors:
                    st.text(err)


# ════════════════════════════════════════════════════════════════════════════════
# TAB 2: Assessment
# ════════════════════════════════════════════════════════════════════════════════

with tab_assess:
    st.markdown("### 🤖 AI Release Evidence Assessment")
    st.markdown(
        f"Run the AI agent to assess release **{target_release}** readiness. "
        "The agent retrieves evidence **exclusively from Jira** — it never reads the JSON input file."
    )

    col_a, col_b = st.columns(2)
    with col_a:
        st.info(
            f"**Target:** `{target_release}` / `{target_commit}` / `{target_service}` / `{target_env}`"
        )
    with col_b:
        st.info(        f"**Provider / model:** `{llm_provider}` / `{llm_model}`")

    if st.button("▶️ Run Assessment", type="primary", key="btn_assess"):
        creds_ok, creds_msg = _check_jira_credentials()
        if not creds_ok:
            st.error(f"❌ Jira credentials missing: {creds_msg}")
            st.stop()

        from agent.agent import run_assessment
        from reports.generator import generate_markdown_report, generate_json_report

        progress = st.progress(0, text="Connecting to Jira...")
        status_placeholder = st.empty()

        client = None
        try:
            client = _get_jira_client()
            progress.progress(10, text="Starting assessment agent...")

            start = time.time()
            trace = run_assessment(
                client=client,
                project_key=jira_project,
                label=dataset_label,
                release=target_release,
                commit=target_commit,
                service=target_service,
                environment=target_env,
                prompt_version=target_prompt,
                model=llm_model,
                ollama_base_url=ollama_url,
                provider=llm_provider,
            )
            progress.progress(90, text="Generating reports...")

        except Exception as e:
            progress.empty()
            st.error(f"Assessment failed: {e}")
            st.stop()
        finally:
            if client:
                client.close()

        # Save reports
        ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        md_path = Path(reports_dir) / f"assessment_{ts}.md"
        json_path = Path(reports_dir) / f"assessment_{ts}.json"

        try:
            generate_markdown_report(trace, None, md_path)
            generate_json_report(trace, None, json_path)
        except Exception as e:
            st.warning(f"Report generation issue: {e}")

        progress.progress(100, text="Done!")
        time.sleep(0.5)
        progress.empty()

        # Overall verdict display
        verdict = trace.overall_verdict
        verdict_class = {
            "READY": "verdict-ready",
            "BLOCKED": "verdict-blocked",
            "NEEDS_REVIEW": "verdict-needs-review",
        }.get(verdict, "verdict-needs-review")

        verdict_emoji = {"READY": "🟢", "BLOCKED": "🔴", "NEEDS_REVIEW": "🟡"}.get(verdict, "⚪")

        st.markdown(
            f'<div class="verdict-card {verdict_class}">'
            f'<h2>{verdict_emoji} Overall Verdict: {verdict}</h2>'
            f'<p>{trace.overall_reasoning or "See per-requirement details below."}</p>'
            f"</div>",
            unsafe_allow_html=True,
        )

        # Metrics row
        col1, col2, col3, col4 = st.columns(4)
        with col1:
            st.metric("⏱️ Runtime", f"{trace.duration_seconds:.1f}s")
        with col2:
            st.metric("🔧 Tool Calls", len(trace.tool_calls))
        with col3:
            st.metric("📋 Requirements", len(trace.requirements))
        with col4:
            if trace.total_tokens:
                st.metric("🪙 Tokens", trace.total_tokens)

        if trace.error:
            st.warning(f"Agent note: {trace.error}")

        # Per-requirement details
        if trace.requirements:
            st.markdown("### 📋 Requirements")
            for req in trace.requirements:
                verdict_icon = {"PASS": "✅", "FAIL": "❌", "INSUFFICIENT_EVIDENCE": "⚠️"}.get(
                    req.verdict, "?"
                )
                with st.expander(
                    f"{verdict_icon} {req.req_id} – {req.summary} [{req.verdict}]",
                    expanded=(req.verdict in ("FAIL",)),
                ):
                    st.markdown(f"**Verdict:** {req.verdict}")
                    st.markdown(f"**Reasoning:** {req.reasoning}")
                    if req.evidence_refs:
                        st.markdown(f"**Evidence refs:** `{'`, `'.join(req.evidence_refs)}`")
                    if req.outstanding:
                        st.warning(f"Outstanding: {req.outstanding}")

        # Tool call trace
        if trace.tool_calls:
            with st.expander(f"🔍 Investigation Trace ({len(trace.tool_calls)} tool calls)"):
                for tc in trace.tool_calls:
                    status = "✅" if not tc.error else "❌"
                    st.markdown(
                        f"**{tc.call_index + 1}.** {status} `{tc.tool_name}` "
                        f"({tc.duration_ms:.0f}ms) — args: `{json.dumps(tc.tool_args)}`"
                    )

        # Report download links
        if md_path.exists():
            st.download_button(
                "📥 Download Markdown Report",
                md_path.read_text(encoding="utf-8"),
                file_name=md_path.name,
                mime="text/markdown",
                key="dl_md",
            )
        if json_path.exists():
            st.download_button(
                "📥 Download JSON Report",
                json_path.read_text(encoding="utf-8"),
                file_name=json_path.name,
                mime="application/json",
                key="dl_json",
            )


# ════════════════════════════════════════════════════════════════════════════════
# TAB 3: Reports
# ════════════════════════════════════════════════════════════════════════════════

with tab_reports:
    st.markdown("### 📊 Previous Reports")

    reports_path = Path(reports_dir)
    if not reports_path.exists():
        st.info("No reports directory found yet. Run an assessment to generate reports.")
    else:
        json_reports = sorted(reports_path.glob("assessment_*.json"), reverse=True)
        if not json_reports:
            st.info("No assessment reports found. Run an assessment first.")
        else:
            selected = st.selectbox(
                "Select report",
                json_reports,
                format_func=lambda p: p.name,
                key="report_select",
            )
            if selected:
                try:
                    report_data = json.loads(selected.read_text(encoding="utf-8"))
                    st.json(report_data, expanded=False)

                    overall = report_data.get("overall_verdict", "UNKNOWN")
                    verdict_emoji = {"READY": "🟢", "BLOCKED": "🔴", "NEEDS_REVIEW": "🟡"}.get(
                        overall, "⚪"
                    )
                    st.markdown(f"### {verdict_emoji} {overall}")
                    st.markdown(report_data.get("overall_reasoning", ""))

                    md_path = selected.with_suffix(".md")
                    if md_path.exists():
                        st.download_button(
                            "📥 Download Markdown",
                            md_path.read_text(encoding="utf-8"),
                            file_name=md_path.name,
                            key="dl_md_hist",
                        )
                except Exception as e:
                    st.error(f"Error reading report: {e}")


# ════════════════════════════════════════════════════════════════════════════════
# TAB 4: About
# ════════════════════════════════════════════════════════════════════════════════

with tab_about:
    st.markdown(
        """
### 🟡 The Yellow Network – Jira Release Evidence Agent

A proof-of-concept (POC) demonstrating AI-driven release evidence assessment using:

| Component | Technology |
|-----------|-----------|
| **Language** | Python 3.11+ |
| **AI Agent** | Gemini API or local Ollama with individual Jira tool calls |
| **Issue Tracking** | Jira Cloud REST API v3 |
| **Data Models** | Pydantic v2 |
| **State Storage** | SQLite import mappings; Jira stores source evidence |
| **Frontend** | Streamlit |
| **Testing** | pytest |

### Architecture

```
dataset.json ──▶ Importer ──▶ Jira Project
                                    │
                         AI Agent (Gemini / Ollama)
                                    │  (tool calls)
                              Jira Tools
                                    │
                    Evidence checks + aggregation
                                    │
                           Markdown + JSON Report
```

### Key Design Principles

1. **Separation of concerns**: Importer reads JSON; Agent never does
2. **Resumable import**: SQLite-tracked; status transitions are retried on reruns
3. **Bounded tool loop**: Max 100 tool calls per assessment
4. **Evidence checks**: Separate Jira tools check scope, verification, approval and defects
5. **Evidence applicability**: Release, commit, service, environment, prompt all verified
6. **Supersession handling**: Later QA retests override earlier failed runs
7. **No credential hardcoding**: .env only

### Limitations (POC)

- Requires real Jira credentials for import and assessment
- Gemini requires `GEMINI_API_KEY`; Ollama requires a running server and a pulled model
- Jira workflow must expose a transition to each recorded status
- Assessment quality depends on model's tool-calling capability
- Large projects may hit token limits; adjust `MAX_TOOL_CALLS` in `agent/agent.py`
        """
    )
