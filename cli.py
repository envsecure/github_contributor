from __future__ import annotations

import sys

from rich.console import Console
from rich.panel import Panel
from rich.prompt import Prompt
from rich.markdown import Markdown
from rich.table import Table
from rich import box

from config import settings
from context import SessionContext
from agent.state import AgentState
from agent.graph import build_analysis_graph, build_execution_graph
from agent.tools import parse_repos, fetch_open_issues
from models.provider import GeminiProvider

console = Console()
ctx = SessionContext()


def banner() -> None:
    console.print("""
╔══════════════════════════════════════════╗
║     OSS CONTRIBUTOR AGENT v2            ║
║     Gemini + LangGraph                   ║
╚══════════════════════════════════════════╝
""", style="bold cyan")


def check_config() -> bool:
    missing = []
    if not settings.GITHUB_TOKEN:
        missing.append("GITHUB_TOKEN")
    if not settings.GEMINI_API_KEY:
        missing.append("GEMINI_API_KEY")
    if missing:
        console.print(f"[red]Missing: {', '.join(missing)}[/red]")
        return False
    return True


def choose_model() -> str:
    console.print("\n[bold]Models:[/bold]")
    for i, m in enumerate(GeminiProvider.MODELS, 1):
        console.print(f"  {i}. {m}")
    idx = Prompt.ask("Select", default="1")
    try:
        return GeminiProvider.MODELS[max(0, int(idx) - 1)]
    except (ValueError, IndexError):
        return GeminiProvider.MODELS[0]


def collect_repos() -> list[str]:
    console.print("\n[bold]Enter repos[/bold] (URL or owner/name, blank line to finish):")
    lines = []
    while True:
        line = input("  > ").strip()
        if not line:
            break
        lines.append(line)
    repos = parse_repos("\n".join(lines))
    if not repos:
        console.print("[yellow]Try again.[/yellow]")
        return collect_repos()
    return repos


def pick_repo(repos: list[str]) -> str:
    if len(repos) == 1:
        return repos[0]
    for i, r in enumerate(repos, 1):
        console.print(f"  {i}. {r}")
    idx = Prompt.ask("Pick repo", default="1")
    try:
        return repos[int(idx) - 1]
    except (ValueError, IndexError):
        return repos[0]


def pick_issue(repo: str) -> int:
    console.print(f"\n[bold]Issues for {repo}:[/bold]")
    issues = fetch_open_issues(repo)
    if not issues:
        console.print("[yellow]None found. Will analyze code only.[/yellow]")
        return 0
    table = Table(box=box.SIMPLE)
    table.add_column("#", style="cyan")
    table.add_column("Title")
    table.add_column("Labels", style="green")
    for i in issues:
        table.add_row(str(i["number"]), i["title"][:60], ", ".join(i["labels"][:3]))
    console.print(table)
    val = Prompt.ask("Issue number (or 0 to skip)", default=str(issues[0]["number"]))
    try:
        return int(val)
    except ValueError:
        return issues[0]["number"]


def show_plan(text: str) -> None:
    console.print("\n[bold cyan]Proposed Plan[/bold cyan]")
    console.print(Panel(Markdown(text), border_style="cyan"))


def plan_menu() -> tuple[bool, str]:
    console.print("\n  [green]a[/green] — Approve & execute")
    console.print("  [yellow]e[/yellow] — Edit plan")
    console.print("  [red]c[/red] — Cancel")
    c = Prompt.ask("Choice", default="a")
    if c == "a":
        return True, ""
    if c == "e":
        return False, Prompt.ask("What to change?")
    return False, "CANCELLED"


def run() -> None:
    banner()
    if not check_config():
        sys.exit(1)

    model = choose_model()
    repos = collect_repos()
    repo = pick_repo(repos)
    issue = pick_issue(repo)
    console.print(f"\n[bold green]→ {repo}[/bold green] | Issue #{issue} | {model}\n")

    # ── Phase 1: Analysis ──────────────────────────────────
    analysis_graph = build_analysis_graph(ctx)
    plan = ""

    for event in analysis_graph.stream(
        AgentState(
            raw_input=repo,
            repos=repos,
            selected_model=model,
            selected_repo=repo,
            selected_issue=issue,
        ),
        {"recursion_limit": settings.MAX_STEPS},
    ):
        for node, updates in event.items():
            if updates.get("error"):
                console.print(f"[red]Error [{node}]: {updates['error']}[/red]")
                return
            if updates.get("step") == "analyzed":
                details = updates.get("issue_details", "")
                console.print(f"[green]Analysis complete.[/green]")
                if details:
                    console.print(Markdown(f"**Issues:**\n{details}"))
            if updates.get("step") == "plan_ready":
                plan = updates.get("proposed_plan", "")

    if not plan:
        console.print("[yellow]No plan generated.[/yellow]")
        return

    # ── Phase 2: User Approval ─────────────────────────────
    show_plan(plan)
    approved, feedback = plan_menu()

    if approved:
        console.print("[green]Approved. Executing...[/green]")
    elif feedback == "CANCELLED":
        console.print("[yellow]Cancelled.[/yellow]")
        return
    else:
        console.print("[yellow]Edit requested. Regenerate with feedback...[/yellow]")
        ctx.user_feedback = feedback
        for event in analysis_graph.stream(
            AgentState(
                raw_input=repo,
                repos=repos,
                selected_model=model,
                selected_repo=repo,
                selected_issue=issue,
                user_feedback=feedback,
            ),
            {"recursion_limit": settings.MAX_STEPS},
        ):
            for node, updates in event.items():
                if updates.get("step") == "plan_ready":
                    plan = updates.get("proposed_plan", "")
                    show_plan(plan)
                    approved, _ = plan_menu()
                    if not approved:
                        console.print("[yellow]Cancelled.[/yellow]")
                        return

    # ── Phase 3: Execution ─────────────────────────────────
    exec_graph = build_execution_graph(ctx)
    for event in exec_graph.stream(
        AgentState(
            repos=repos,
            selected_model=model,
            selected_repo=repo,
            selected_issue=issue,
            proposed_plan=plan,
            plan_approved=True,
            issues_found=ctx.issues,
            step="plan_ready",
        ),
        {"recursion_limit": settings.MAX_STEPS},
    ):
        for node, updates in event.items():
            if updates.get("error"):
                console.print(f"[red]Error [{node}]: {updates['error']}[/red]")
                return
            pr_url = updates.get("pr_url")
            if pr_url:
                console.print(f"\n[bold green]PR created:[/bold green] {pr_url}")
                return

    console.print("[bold]Done.[/bold]")


def main() -> None:
    try:
        run()
    except KeyboardInterrupt:
        console.print("\n[yellow]Interrupted.[/yellow]")
        sys.exit(0)
    except Exception as e:
        console.print(f"\n[red]{e}[/red]")
        sys.exit(1)


if __name__ == "__main__":
    main()
