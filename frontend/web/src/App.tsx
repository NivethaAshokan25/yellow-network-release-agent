import { useCallback, useEffect, useMemo, useState } from "react";

type Verdict = "PASS" | "FAIL" | "INSUFFICIENT_EVIDENCE" | string;
type Filter = "ALL" | "PASS" | "FAIL" | "INSUFFICIENT_EVIDENCE";

interface AssessmentTarget {
  release?: string;
  commit?: string;
  service?: string;
  environment?: string;
  prompt?: string;
}

interface Requirement {
  req_id: string;
  summary: string;
  mandatory?: boolean;
  verdict: Verdict;
  reasoning?: string;
  outstanding?: string;
  jira_refs?: string[];
  evidence_refs?: string[];
  evidence_checks?: Array<{
    source_ref: string;
    verdict: Verdict;
    reasoning: string;
  }>;
}

interface ToolCall {
  index: number;
  tool: string;
  args: Record<string, unknown>;
  result: unknown;
  error?: string | null;
  duration_ms?: number;
}

interface ReportSummary {
  id: string;
  generated_at?: string;
  assessment?: {
    project_key?: string;
    label?: string;
    target?: AssessmentTarget;
    model?: string;
    provider?: string;
    runtime_seconds?: number;
    total_tokens?: number;
  };
  overall_verdict: string;
  requirements_count: number;
  tool_call_count: number;
  agent_error?: string | null;
}

interface Report extends ReportSummary {
  overall_reasoning?: string;
  requirements: Requirement[];
  tool_calls?: ToolCall[];
  jira_base_url?: string;
}

interface Job {
  id: string;
  kind: "import" | "assessment";
  status: "queued" | "running" | "completed" | "failed";
  phase?: string;
  completed?: number;
  total?: number;
  detail?: string;
  report_id?: string;
  warning?: string | null;
  error?: string;
  result?: ImportResult;
}

interface JobStart {
  job_id: string;
  status: Job["status"];
  kind: Job["kind"];
}

interface DatasetPreview {
  dataset_label: string;
  records: number;
  requirements: number;
  release?: string;
}

interface ImportResult {
  dataset_label: string;
  records_total: number;
  change_count: number;
  created: Array<{ source_id: string; issue_key: string }>;
  updated: Array<{ source_id: string; issue_key: string }>;
  skipped: string[];
  failed: Array<{ source_id: string; error: string }>;
  comments_added: number;
  comment_errors: Array<{ source_id: string; issue_key: string; error: string }>;
  links_created: Array<{ source_id: string; linked_source_id: string }>;
  link_errors: string[];
  status_updates: Array<{ source_id: string; issue_key: string }>;
  status_errors: Array<{ source_id: string; issue_key: string; error: string }>;
}

interface ReportsResponse {
  reports: ReportSummary[];
  jira_base_url: string;
}

const FILTERS: Filter[] = ["ALL", "PASS", "FAIL", "INSUFFICIENT_EVIDENCE"];

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const response = await fetch(url, {
    ...init,
    headers: {
      ...(init?.headers ?? {}),
      ...(init?.body ? { "Content-Type": "application/json" } : {}),
    },
  });
  if (!response.ok) {
    let detail = `Request failed (${response.status})`;
    try {
      const body = (await response.json()) as { detail?: string };
      detail = body.detail ?? detail;
    } catch {
      detail = response.statusText || detail;
    }
    throw new Error(detail);
  }
  return (await response.json()) as T;
}

function formatDate(value?: string): string {
  if (!value) return "Unknown date";
  const date = new Date(value);
  return Number.isNaN(date.getTime())
    ? value
    : date.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function jiraHref(reference: string, baseUrl?: string): string | null {
  if (!baseUrl || !/^[A-Z][A-Z0-9_]*-\d+$/i.test(reference)) return null;
  try {
    const base = new URL(baseUrl);
    if (base.protocol !== "https:" && base.protocol !== "http:") return null;
    return new URL(`/browse/${encodeURIComponent(reference)}`, base).toString();
  } catch {
    return null;
  }
}

function isJsonObject(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function VerdictBadge({ verdict }: { verdict: string }) {
  const className = verdict.toLowerCase().replaceAll("_", "-");
  return <span className={`verdict-badge verdict-${className}`}>{verdict.replaceAll("_", " ")}</span>;
}

function App() {
  const [activeTab, setActiveTab] = useState<"import" | "assessment">("import");
  const [reports, setReports] = useState<ReportSummary[]>([]);
  const [selectedReportId, setSelectedReportId] = useState("");
  const [report, setReport] = useState<Report | null>(null);
  const [jiraBaseUrl, setJiraBaseUrl] = useState("");
  const [filter, setFilter] = useState<Filter>("ALL");
  const [project, setProject] = useState("TYN");
  const [label, setLabel] = useState("tyn-release-assessment-v2");
  const [release, setRelease] = useState("R2.4");
  const [provider, setProvider] = useState<"gemini" | "ollama">("gemini");
  const [model, setModel] = useState("");
  const [selectedFile, setSelectedFile] = useState<File | null>(null);
  const [datasetJson, setDatasetJson] = useState<Record<string, unknown> | null>(null);
  const [datasetPreview, setDatasetPreview] = useState<DatasetPreview | null>(null);
  const [importResult, setImportResult] = useState<ImportResult | null>(null);
  const [jobId, setJobId] = useState("");
  const [job, setJob] = useState<Job | null>(null);
  const [loading, setLoading] = useState(true);
  const [rerunning, setRerunning] = useState(false);
  const [importing, setImporting] = useState(false);
  const [error, setError] = useState("");

  const loadReport = useCallback(async (id: string) => {
    const result = await requestJson<Report>(`/api/reports/${encodeURIComponent(id)}`);
    setReport(result);
    setSelectedReportId(id);
    setJiraBaseUrl(result.jira_base_url ?? "");
  }, []);

  const loadReports = useCallback(async (preferredId?: string) => {
    const result = await requestJson<ReportsResponse>("/api/reports");
    setReports(result.reports);
    setJiraBaseUrl(result.jira_base_url);
    const nextId =
      preferredId && result.reports.some((item) => item.id === preferredId)
        ? preferredId
        : result.reports[0]?.id;
    if (nextId) {
      await loadReport(nextId);
    } else {
      setReport(null);
      setSelectedReportId("");
    }
  }, [loadReport]);

  useEffect(() => {
    let active = true;
    void loadReports()
      .catch((cause: unknown) => {
        if (active) setError(cause instanceof Error ? cause.message : "Could not load reports.");
      })
      .finally(() => {
        if (active) setLoading(false);
      });
    return () => {
      active = false;
    };
  }, [loadReports]);

  useEffect(() => {
    if (!jobId) return;
    let active = true;
    let timer: number | undefined;

    const poll = async () => {
      try {
        const current = await requestJson<Job>(`/api/jobs/${encodeURIComponent(jobId)}`);
        if (!active) return;
        setJob(current);
        if (current.status === "completed") {
          if (current.kind === "import") {
            const result = current.result;
            if (!result) {
              setJobId("");
              setError("Import finished without returning an import summary.");
              return;
            }
            setImportResult(result);
            const hasImportErrors = Boolean(
              result.failed.length
              || result.comment_errors.length
              || result.link_errors.length
              || result.status_errors.length,
            );
            if (result.change_count > 0 && !hasImportErrors) {
              setActiveTab("assessment");
              setError("");
              try {
                const started = await queueAssessment();
                if (!active) return;
                setJob({ id: started.job_id, kind: "assessment", status: started.status });
                setJobId(started.job_id);
              } catch (cause) {
                if (active) {
                  setJobId("");
                  setError(
                    `Import succeeded, but the follow-up assessment could not start: ${
                      cause instanceof Error ? cause.message : "unknown error"
                    }`,
                  );
                }
              }
              return;
            }
            setJobId("");
            if (hasImportErrors) {
              setError("Import completed with errors. Review them before trusting or rerunning the assessment.");
            }
            return;
          }
          setJobId("");
          if (current.warning) setError(`Assessment report was saved with an agent warning: ${current.warning}`);
          await loadReports(current.report_id);
          return;
        }
        if (current.status === "failed") {
          setJobId("");
          setError(current.error ?? "Assessment failed without an error message.");
          return;
        }
        timer = window.setTimeout(() => void poll(), 1200);
      } catch (cause) {
        if (active) {
          setJobId("");
          setError(cause instanceof Error ? cause.message : "Could not check assessment status.");
        }
      }
    };

    void poll();
    return () => {
      active = false;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [jobId, loadReports]);

  const visibleRequirements = useMemo(() => {
    const requirements = report?.requirements ?? [];
    return filter === "ALL"
      ? requirements
      : requirements.filter((item) => item.verdict === filter);
  }, [filter, report]);

  const counts = useMemo(() => {
    const requirements = report?.requirements ?? [];
    return {
      total: requirements.length,
      passed: requirements.filter((item) => item.verdict === "PASS").length,
      failed: requirements.filter((item) => item.verdict === "FAIL").length,
      review: requirements.filter((item) => item.verdict === "INSUFFICIENT_EVIDENCE").length,
    };
  }, [report]);

  async function rerunAssessment(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    setRerunning(true);
    setError("");
    setJob(null);
    try {
      const started = await queueAssessment();
      setJob({ id: started.job_id, kind: "assessment", status: started.status });
      setJobId(started.job_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start the assessment.");
    } finally {
      setRerunning(false);
    }
  }

  async function queueAssessment(): Promise<JobStart> {
    return requestJson<JobStart>("/api/assessments", {
      method: "POST",
      body: JSON.stringify({
        project,
        label,
        release,
        provider,
        ...(model.trim() ? { model: model.trim() } : {}),
      }),
    });
  }

  async function chooseDatasetFile(event: React.ChangeEvent<HTMLInputElement>) {
    const file = event.currentTarget.files?.[0] ?? null;
    setSelectedFile(file);
    setDatasetJson(null);
    setDatasetPreview(null);
    setImportResult(null);
    setError("");
    if (!file) return;
    if (file.size > 20 * 1024 * 1024) {
      setError("Dataset JSON must be smaller than 20 MB.");
      return;
    }

    try {
      const parsed: unknown = JSON.parse(await file.text());
      if (!isJsonObject(parsed) || typeof parsed.dataset_label !== "string" || !Array.isArray(parsed.records)) {
        throw new Error("The file must contain a dataset_label and records array.");
      }
      const records: unknown[] = parsed.records;
      const target = isJsonObject(parsed.target) ? parsed.target : {};
      const preview = {
        dataset_label: parsed.dataset_label,
        records: records.length,
        requirements: records.filter(
          (record) => isJsonObject(record) && record.kind === "requirement",
        ).length,
        release: typeof target.release === "string" ? target.release : undefined,
      };
      setDatasetJson(parsed);
      setDatasetPreview(preview);
      setLabel(preview.dataset_label);
      if (preview.release) setRelease(preview.release);
    } catch (cause) {
      setError(cause instanceof Error ? `Cannot read dataset JSON: ${cause.message}` : "Cannot read dataset JSON.");
    }
  }

  async function importDataset(event: React.FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (!datasetJson) {
      setError("Choose a valid dataset JSON file before importing.");
      return;
    }
    setImporting(true);
    setError("");
    setImportResult(null);
    try {
      const started = await requestJson<JobStart>("/api/imports", {
        method: "POST",
        body: JSON.stringify({ project, dataset: datasetJson }),
      });
      setJob({ id: started.job_id, kind: "import", status: started.status });
      setJobId(started.job_id);
    } catch (cause) {
      setError(cause instanceof Error ? cause.message : "Could not start the import.");
    } finally {
      setImporting(false);
    }
  }

  function chooseReport(id: string) {
    setError("");
    void loadReport(id).catch((cause: unknown) => {
      setError(cause instanceof Error ? cause.message : "Could not load the selected report.");
    });
  }

  const target = report?.assessment?.target;
  const isBusy = rerunning || importing || Boolean(jobId);

  return (
    <main className="app-shell">
      <header className="topbar">
        <a className="brand" href="/" aria-label="Yellow Network release evidence home">
          <span className="brand-mark">Y</span>
          <span>yellow network <small>RELEASE EVIDENCE</small></span>
        </a>
        <div className="topbar-status"><span className="status-dot" /> Jira assessment workspace</div>
      </header>

      <nav className="work-tabs" role="tablist" aria-label="Release workflow">
        <button
          id="import-tab"
          role="tab"
          aria-selected={activeTab === "import"}
          aria-controls="import-panel"
          className={activeTab === "import" ? "work-tab active" : "work-tab"}
          onClick={() => setActiveTab("import")}
        >
          <span aria-hidden="true">↑</span> Import dataset
        </button>
        <button
          id="assessment-tab"
          role="tab"
          aria-selected={activeTab === "assessment"}
          aria-controls="assessment-panel"
          className={activeTab === "assessment" ? "work-tab active" : "work-tab"}
          onClick={() => setActiveTab("assessment")}
        >
          <span aria-hidden="true">◎</span> Assessment
        </button>
      </nav>

      {error && <div className="alert" role="alert"><strong>Action needed</strong><span>{error}</span><button onClick={() => setError("")} aria-label="Dismiss error">×</button></div>}
      {job && jobId && <div className="job-banner" role="status">
        <span className="spinner" />
        <div className="job-copy">
          <strong>{job.kind === "import" ? "Importing Jira records" : "Assessing release evidence"}</strong>
          <span>{job.kind === "import"
            ? job.detail ?? `${job.phase ?? "Preparing"}: ${job.completed ?? 0}/${job.total ?? 0}`
            : job.status === "queued" ? "Assessment queued…" : "The agent is checking scoped Jira evidence."}</span>
        </div>
        {job.kind === "import" && job.total != null && job.total > 0 && (
          <progress value={job.completed ?? 0} max={job.total} aria-label="Import progress" />
        )}
      </div>}

      {activeTab === "import" ? (
        <section id="import-panel" className="workflow-panel" role="tabpanel" aria-labelledby="import-tab">
          <div className="page-heading import-heading">
            <div>
              <p className="eyebrow">STEP 1 · IMPORT JIRA EVIDENCE</p>
              <h1>Keep the project in sync.</h1>
              <p className="intro">Upload the dataset JSON to create new Jira records and update mapped records without duplicating comments.</p>
            </div>
          </div>
          <div className="import-layout">
            <form className="import-card" onSubmit={(event) => void importDataset(event)}>
              <div className="import-card-title">
                <span className="upload-icon" aria-hidden="true">↑</span>
                <div><h2>Dataset file</h2><p>Choose the source JSON to import or re-import.</p></div>
              </div>
              <label className="file-picker" htmlFor="dataset-json">
                <input
                  id="dataset-json"
                  type="file"
                  accept=".json,application/json"
                  onChange={(event) => void chooseDatasetFile(event)}
                />
                <span className="file-picker-icon" aria-hidden="true">＋</span>
                <strong>{selectedFile?.name ?? "Choose a JSON file"}</strong>
                <small>{selectedFile ? `${(selectedFile.size / 1024).toFixed(1)} KB` : "JSON format · up to 20 MB"}</small>
              </label>
              {datasetPreview && (
                <div className="dataset-preview">
                  <div><span>Dataset scope</span><strong>{datasetPreview.dataset_label}</strong></div>
                  <div><span>Records</span><strong>{datasetPreview.records}</strong></div>
                  <div><span>Requirements</span><strong>{datasetPreview.requirements}</strong></div>
                  <div><span>Target release</span><strong>{datasetPreview.release ?? "—"}</strong></div>
                </div>
              )}
              <label className="project-field">
                <span>Jira project key</span>
                <input value={project} onChange={(event) => setProject(event.target.value)} required maxLength={32} />
              </label>
              <button type="submit" className="primary-button import-button" disabled={!datasetJson || isBusy}>
                {importing || (job?.kind === "import" && Boolean(jobId))
                  ? <><span className="spinner" /> Importing…</>
                  : <><span aria-hidden="true">↻</span> Import or update Jira records</>}
              </button>
            </form>
            <aside className="import-guidance">
              <p className="eyebrow">SAFE RE-IMPORT</p>
              <h2>Update what exists. Create what’s new.</h2>
              <ul>
                <li><span>01</span><p>Source IDs map the JSON records to their existing Jira issues.</p></li>
                <li><span>02</span><p>Changed summaries, descriptions, labels, and statuses are synchronized.</p></li>
                <li><span>03</span><p>Only missing comments and issue links are added; existing evidence is retained.</p></li>
              </ul>
              <div className="auto-assess-note"><span aria-hidden="true">↻</span> A successful import with changes automatically starts a fresh assessment.</div>
            </aside>
          </div>
          {importResult && (
            <section className="import-result" aria-live="polite">
              <div className="import-result-heading">
                <div><p className="eyebrow">IMPORT RESULT</p><h2>{importResult.change_count ? "Changes applied" : "No Jira changes found"}</h2></div>
                <span className={importResult.failed.length || importResult.comment_errors.length || importResult.link_errors.length || importResult.status_errors.length ? "result-warning" : "result-success"}>
                  {importResult.failed.length || importResult.comment_errors.length || importResult.link_errors.length || importResult.status_errors.length ? "Review errors" : "Completed"}
                </span>
              </div>
              <div className="import-metrics">
                <div><span>Created</span><strong>{importResult.created.length}</strong></div>
                <div><span>Updated</span><strong>{importResult.updated.length}</strong></div>
                <div><span>Unchanged</span><strong>{importResult.skipped.length}</strong></div>
                <div><span>Comments added</span><strong>{importResult.comments_added}</strong></div>
                <div><span>Links added</span><strong>{importResult.links_created.length}</strong></div>
              </div>
              {importResult.change_count > 0 && !importResult.failed.length && !importResult.comment_errors.length && !importResult.link_errors.length && !importResult.status_errors.length && (
                <p className="import-followup">Jira changed. A new assessment is running in the Assessment tab.</p>
              )}
              {(importResult.failed.length > 0 || importResult.comment_errors.length > 0 || importResult.link_errors.length > 0 || importResult.status_errors.length > 0) && (
                <ul className="import-errors">
                  {importResult.failed.map((item) => <li key={`failed-${item.source_id}`}><strong>{item.source_id}</strong> {item.error}</li>)}
                  {importResult.comment_errors.map((item, index) => <li key={`comment-${item.source_id}-${index}`}><strong>{item.source_id}</strong> {item.error}</li>)}
                  {importResult.link_errors.map((item, index) => <li key={`link-${index}`}>{item}</li>)}
                  {importResult.status_errors.map((item, index) => <li key={`status-${item.source_id}-${index}`}><strong>{item.source_id}</strong> {item.error}</li>)}
                </ul>
              )}
            </section>
          )}
        </section>
      ) : (
      <section id="assessment-panel" className="workflow-panel" role="tabpanel" aria-labelledby="assessment-tab">
      <section className="page-heading">
        <div>
          <p className="eyebrow">ENGINEERING · RELEASE READINESS</p>
          <h1>Evidence, not assumptions.</h1>
          <p className="intro">Review requirement decisions grounded in Jira records and rerun when evidence changes.</p>
        </div>
        <form className="rerun-form" onSubmit={(event) => void rerunAssessment(event)}>
          <label>
            <span>Jira project</span>
            <input value={project} onChange={(event) => setProject(event.target.value)} required maxLength={32} />
          </label>
          <label>
            <span>Dataset scope label</span>
            <input value={label} onChange={(event) => setLabel(event.target.value)} required />
          </label>
          <label>
            <span>Target release</span>
            <input value={release} onChange={(event) => setRelease(event.target.value)} required />
          </label>
          <label>
            <span>Model provider</span>
            <select value={provider} onChange={(event) => setProvider(event.target.value as "gemini" | "ollama")}>
              <option value="gemini">Gemini</option>
              <option value="ollama">Ollama</option>
            </select>
          </label>
          <label>
            <span>Model (optional)</span>
            <input value={model} onChange={(event) => setModel(event.target.value)} placeholder="Use provider default" />
          </label>
          <button type="submit" className="primary-button" disabled={isBusy}>
            {isBusy ? <><span className="spinner" /> Assessing…</> : <><span aria-hidden="true">↻</span> Rerun assessment</>}
          </button>
        </form>
      </section>

      <section className="report-toolbar">
        <div>
          <p className="eyebrow">ASSESSMENT REPORT</p>
          {report ? <h2>{report.assessment?.project_key ?? "Jira project"} <span className="slash">/</span> {target?.release ?? "Release"}</h2> : <h2>Report history</h2>}
          <p className="muted">{report ? `Generated ${formatDate(report.generated_at)} · ${report.assessment?.provider ?? "Unknown provider"} / ${report.assessment?.model ?? "Unknown model"}` : "Select or generate an assessment report."}</p>
        </div>
        <div className="history-control">
          <label htmlFor="report-select">Report history</label>
          <select
            id="report-select"
            value={selectedReportId}
            onChange={(event) => chooseReport(event.target.value)}
            disabled={!reports.length}
          >
            {reports.length === 0 && <option value="">No reports yet</option>}
            {reports.map((item) => <option key={item.id} value={item.id}>{formatDate(item.generated_at)} · {item.overall_verdict}</option>)}
          </select>
        </div>
      </section>

      {loading ? (
        <section className="empty-state"><span className="spinner" /> Loading reports…</section>
      ) : !report ? (
        <section className="empty-state">
          <span className="empty-icon">◎</span>
          <h2>No assessment report yet</h2>
          <p>Start a rerun to inspect the scoped Jira records and create the first report.</p>
        </section>
      ) : (
        <>
          <section className="overview-grid" aria-label="Assessment summary">
            <article className={`overall-card overall-${report.overall_verdict.toLowerCase().replaceAll("_", "-")}`}>
              <div className="overall-top"><span className="eyebrow">OVERALL VERDICT</span><span className="sparkle">✳</span></div>
              <h2>{report.overall_verdict.replaceAll("_", " ")}</h2>
              <p>{report.overall_reasoning || "No overall reasoning was recorded."}</p>
              {report.agent_error && <p className="inline-warning">Agent warning: {report.agent_error}</p>}
            </article>
            <article className="metric-card"><span className="metric-label">Requirements</span><strong>{counts.total}</strong><span className="metric-note">in this assessment</span></article>
            <article className="metric-card"><span className="metric-label">Passed</span><strong className="metric-pass">{counts.passed}</strong><span className="metric-note">evidence supports criteria</span></article>
            <article className="metric-card"><span className="metric-label">Need attention</span><strong className="metric-review">{counts.failed + counts.review}</strong><span className="metric-note">{counts.failed} failed · {counts.review} need evidence</span></article>
          </section>

          <section className="metadata-strip" aria-label="Release target details">
            <div><span>Target commit</span><strong>{target?.commit ?? "—"}</strong></div>
            <div><span>Service</span><strong>{target?.service ?? "—"}</strong></div>
            <div><span>Environment</span><strong>{target?.environment ?? "—"}</strong></div>
            <div><span>Prompt version</span><strong>{target?.prompt ?? "—"}</strong></div>
            <div><span>Runtime</span><strong>{report.assessment?.runtime_seconds ?? "—"}{report.assessment?.runtime_seconds != null ? "s" : ""}</strong></div>
            <div><span>Tool calls</span><strong>{report.tool_call_count}</strong></div>
          </section>

          <section className="requirements-section">
            <div className="section-heading">
              <div><p className="eyebrow">DECISION BREAKDOWN</p><h2>Requirements <span className="count-pill">{visibleRequirements.length}</span></h2></div>
              <label className="filter-control"><span>Filter by verdict</span>
                <select value={filter} onChange={(event) => setFilter(event.target.value as Filter)}>
                  {FILTERS.map((item) => <option key={item} value={item}>{item === "ALL" ? "All decisions" : item.replaceAll("_", " ")}</option>)}
                </select>
              </label>
            </div>
            <div className="requirement-list">
              {visibleRequirements.length ? visibleRequirements.map((item) => (
                <article className="requirement-card" key={`${item.req_id}-${item.summary}`}>
                  <div className="requirement-top">
                    <div className="requirement-title"><span className="req-id">{item.req_id}</span><h3>{item.summary}</h3>{item.mandatory && <span className="mandatory">MANDATORY</span>}</div>
                    <VerdictBadge verdict={item.verdict} />
                  </div>
                  {item.reasoning && <p className="reasoning">{item.reasoning}</p>}
                  {item.outstanding && <p className="outstanding"><strong>Outstanding:</strong> {item.outstanding}</p>}
                  <div className="evidence-row">
                    <span className="evidence-label">JIRA EVIDENCE</span>
                    {(item.jira_refs ?? item.evidence_refs ?? []).length ? (
                      <div className="jira-links">{(item.jira_refs ?? item.evidence_refs ?? []).map((reference) => {
                        const href = jiraHref(reference, jiraBaseUrl);
                        return href
                          ? <a key={reference} href={href} target="_blank" rel="noreferrer">{reference}<span aria-hidden="true"> ↗</span></a>
                          : <span className="plain-ref" key={reference}>{reference}</span>;
                      })}</div>
                    ) : <span className="muted">No Jira references recorded</span>}
                  </div>
                  {!!item.evidence_checks?.length && <ul className="evidence-checks">{item.evidence_checks.map((check, index) => <li key={`${check.source_ref}-${index}`}><strong>{check.source_ref}</strong><span>{check.verdict.replaceAll("_", " ")}</span><p>{check.reasoning}</p></li>)}</ul>}
                </article>
              )) : <div className="empty-filter">No requirements match this verdict filter.</div>}
            </div>
          </section>

          <details className="trace-panel">
            <summary><span>Investigation trace</span><span className="trace-count">{report.tool_call_count} tool calls</span></summary>
            <div className="trace-list">
              {(report.tool_calls ?? []).map((call) => {
                const resultSummary = JSON.stringify(call.result);
                return (
                  <article className="trace-item" key={`${call.index}-${call.tool}`}>
                    <div><span className="trace-index">{String(call.index + 1).padStart(2, "0")}</span><strong>{call.tool}</strong><span className={call.error ? "trace-failed" : "trace-ok"}>{call.error ? "Failed" : "Completed"}</span><small>{call.duration_ms?.toFixed(0) ?? "—"} ms</small></div>
                    <code>{JSON.stringify(call.args)}</code>
                    {resultSummary && <p className="trace-result">{resultSummary.length > 360 ? `${resultSummary.slice(0, 360)}…` : resultSummary}</p>}
                    {call.error && <p className="inline-warning">{call.error}</p>}
                  </article>
                );
              })}
              {!report.tool_calls?.length && <p className="muted">No tool calls were recorded for this report.</p>}
            </div>
          </details>
          <footer className="page-footer">Decisions reflect evidence retrieved from the scoped Jira dataset. Missing evidence is reported separately from failure.</footer>
        </>
      )}
      </section>
      )}
    </main>
  );
}

export default App;
