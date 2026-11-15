"""
CLI entry point for The Yellow Network – Jira Release Evidence Agent.

Commands:
  import    Import dataset.json into Jira
  assess    Run AI assessment against Jira
  validate  Validate an existing import
  report    Generate a report from a previous assessment trace (JSON)
  web       Start the local dashboard API
"""
from __future__ import annotations

import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import click
from dotenv import load_dotenv
from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

load_dotenv()

console = Console()


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(console=console, rich_tracebacks=True)],
    )


def _get_jira_client():
    """Build JiraClient from environment variables."""
    from jira.client import JiraClient

    base_url = os.environ.get("JIRA_BASE_URL", "")
    email = os.environ.get("JIRA_EMAIL", "")
    token = os.environ.get("JIRA_API_TOKEN", "")

    if not all([base_url, email, token]):
        console.print(
            "[red]Error:[/] JIRA_BASE_URL, JIRA_EMAIL, and JIRA_API_TOKEN must be set in .env"
        )
        sys.exit(1)

    return JiraClient(base_url, email, token)


@click.group()
@click.option("--verbose", "-v", is_flag=True, default=False, help="Enable debug logging")
@click.pass_context
def cli(ctx, verbose):
    """The Yellow Network – Jira Release Evidence Agent"""
    _configure_logging(verbose)
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose


# ── Import Command ─────────────────────────────────────────────────────────────

@cli.command("import")
@click.option(
    "--dataset",
    default=os.environ.get("DATASET_PATH", "inputs/dataset.json"),
    show_default=True,
    help="Path to dataset.json",
)
@click.option(
    "--project-key",
    default=os.environ.get("JIRA_PROJECT_KEY", "TYN"),
    show_default=True,
    help="Jira project key",
)
@click.option(
    "--project-name",
    default=os.environ.get("JIRA_PROJECT_NAME", "Yellow Network Release Assessment"),
    show_default=True,
    help="Jira project name (used if creating new project)",
)
@click.option("--dry-run", is_flag=True, default=False, help="Print what would be imported, no writes")
@click.option(
    "--force",
    is_flag=True,
    default=False,
    help="Clear local import tracking and recreate records (may create Jira duplicates)",
)
@click.option(
    "--update-existing",
    is_flag=True,
    default=False,
    help="Update mapped Jira issues and add only missing comments instead of skipping them",
)
@click.option(
    "--db",
    default=os.environ.get("DB_PATH", "db/state.db"),
    show_default=True,
    help="SQLite state DB path",
)
@click.pass_context
def import_cmd(ctx, dataset, project_key, project_name, dry_run, force, update_existing, db):
    """Import dataset.json into Jira (idempotent)."""
    from importer.loader import load_dataset
    from importer.jira_importer import run_import

    if dry_run:
        console.print("[yellow]DRY-RUN mode — no Jira writes[/]")

    console.print(f"Loading dataset from [bold]{dataset}[/]...")
    try:
        ds = load_dataset(dataset)
    except FileNotFoundError as e:
        console.print(f"[red]Error:[/] {e}")
        sys.exit(1)

    console.print(
        f"Loaded {len(ds.records)} records "
        f"({len(ds.requirements)} requirements, 1 manifest)"
    )

    client = None if dry_run else _get_jira_client()

    if dry_run:
        # Fake client for dry-run
        from unittest.mock import MagicMock
        client = MagicMock()
        client.get_project.return_value = {"key": project_key}

    console.print(f"Importing into project [bold]{project_key}[/]...")
    try:
        result = run_import(
            dataset=ds,
            client=client,
            project_key=project_key,
            project_name=project_name,
            dry_run=dry_run,
            force=force,
            update_existing=update_existing,
            db_path=db,
        )
    finally:
        if not dry_run:
            client.close()

    table = Table(title="Import Result")
    table.add_column("Metric")
    table.add_column("Count", justify="right")
    table.add_row("Created", str(len(result.created)), style="green")
    table.add_row("Updated", str(len(result.updated)), style="cyan")
    table.add_row("Skipped (already imported)", str(len(result.skipped)), style="dim")
    table.add_row("Failed", str(len(result.failed)), style="red" if result.failed else "dim")
    table.add_row(
        "Comment errors",
        str(len(result.comment_errors)),
        style="yellow" if result.comment_errors else "dim",
    )
    table.add_row(
        "Status errors",
        str(len(result.status_errors)),
        style="yellow" if result.status_errors else "dim",
    )
    table.add_row("Link errors", str(len(result.link_errors)), style="yellow" if result.link_errors else "dim")
    console.print(table)

    if result.failed:
        console.print("[red]Failures:[/]")
        for src, err in result.failed:
            console.print(f"  {src}: {err}")

    if result.link_errors:
        console.print("[yellow]Link errors:[/]")
        for err in result.link_errors:
            console.print(f"  {err}")

    if result.comment_errors:
        console.print("[yellow]Comment errors:[/]")
        for source_id, jira_key, err in result.comment_errors:
            console.print(f"  {source_id} ({jira_key}): {err}")

    if result.status_errors:
        console.print("[yellow]Status errors:[/]")
        for source_id, jira_key, err in result.status_errors:
            console.print(f"  {source_id} ({jira_key}): {err}")

    if result.failed or result.comment_errors or result.status_errors or result.link_errors:
        sys.exit(1)

    console.print("[green]Import complete.[/]" if not dry_run else "[yellow]Dry-run complete.[/]")


# ── Validate Command ───────────────────────────────────────────────────────────

@cli.command("validate")
@click.option(
    "--dataset",
    default=os.environ.get("DATASET_PATH", "inputs/dataset.json"),
    show_default=True,
    help="Source dataset used for validation",
)
@click.option("--project", default=os.environ.get("JIRA_PROJECT_KEY", "TYN"), show_default=True)
@click.option("--db", default=os.environ.get("DB_PATH", "db/state.db"), show_default=True)
def validate_cmd(dataset, project, db):
    """Validate imported issues and preserved dataset content in Jira."""
    from importer.loader import load_dataset
    from importer.validator import validate_import

    ds = load_dataset(dataset)
    console.print("Validating import...")
    with _get_jira_client() as client:
        report = validate_import(
            client,
            dataset=ds,
            project_key=project,
            db_path=db,
        )
    console.print(report.summary())
    if not report.passed:
        sys.exit(1)


# ── Assess Command ─────────────────────────────────────────────────────────────

@cli.command("assess")
@click.option("--project", default=os.environ.get("JIRA_PROJECT_KEY", "TYN"), show_default=True)
@click.option("--label", default="tyn-release-assessment-v2", show_default=True)
@click.option("--release", default="R2.4", show_default=True)
@click.option("--commit", default="b7a91f2", show_default=True)
@click.option("--service", default="atlas-assistant", show_default=True)
@click.option("--environment", default="production", show_default=True)
@click.option("--prompt-version", default="p7", show_default=True)
@click.option(
    "--provider",
    type=click.Choice(["gemini", "ollama"], case_sensitive=False),
    default=os.environ.get("LLM_PROVIDER", "gemini"),
    show_default=True,
    help="Model provider for the assessment agent",
)
@click.option(
    "--model",
    default=None,
    help="Model name (defaults to provider-specific environment setting)",
)
@click.option(
    "--ollama-url",
    default=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
    show_default=True,
)
@click.option(
    "--output-dir",
    default=os.environ.get("REPORTS_DIR", "reports"),
    show_default=True,
)
def assess_cmd(
    project,
    label,
    release,
    commit,
    service,
    environment,
    prompt_version,
    provider,
    model,
    ollama_url,
    output_dir,
):
    """Run AI assessment agent against Jira evidence."""
    from agent.agent import run_assessment
    from reports.generator import generate_markdown_report, generate_json_report

    provider = provider.lower()
    model = model or (
        os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
        if provider == "gemini"
        else os.environ.get("OLLAMA_MODEL", "qwen3:4b")
    )
    console.print(f"[bold]Starting assessment:[/] {project} / {label} / {release}")
    console.print(f"Provider: [bold]{provider}[/]  Model: [bold]{model}[/]")
    if provider == "ollama":
        console.print(f"Ollama: {ollama_url}")

    with console.status("Running assessment agent..."):
        with _get_jira_client() as client:
            trace = run_assessment(
                client=client,
                project_key=project,
                label=label,
                release=release,
                commit=commit,
                service=service,
                environment=environment,
                prompt_version=prompt_version,
                model=model,
                ollama_base_url=ollama_url,
                provider=provider,
            )

    console.print(f"Assessment complete in [bold]{trace.duration_seconds:.1f}s[/]")
    console.print(f"Tool calls: {len(trace.tool_calls)}")
    console.print(f"Overall verdict: [bold]{trace.overall_verdict}[/]")

    if trace.error:
        console.print(f"[red]Agent error:[/] {trace.error}")

    # Generate reports
    ts = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    md_path = Path(output_dir) / f"assessment_{ts}.md"
    json_path = Path(output_dir) / f"assessment_{ts}.json"

    generate_markdown_report(trace, None, md_path)
    generate_json_report(trace, None, json_path)

    console.print(f"Markdown report: [link]{md_path}[/link]")
    console.print(f"JSON report: [link]{json_path}[/link]")
    if trace.error:
        sys.exit(1)


# ── Local Dashboard API ───────────────────────────────────────────────────────

@cli.command("web")
@click.option("--host", default="127.0.0.1", show_default=True, help="API bind address")
@click.option(
    "--port",
    default=8000,
    type=click.IntRange(1, 65535),
    show_default=True,
    help="API port",
)
def web_cmd(host, port):
    """Start the local API used by the React dashboard."""
    import uvicorn

    console.print(f"Starting dashboard API at [bold]http://{host}:{port}[/]")
    uvicorn.run("frontend.api:app", host=host, port=port)


if __name__ == "__main__":
    cli()
