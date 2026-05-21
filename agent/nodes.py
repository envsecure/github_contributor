from __future__ import annotations

from agent.state import AgentState
from agent.tools import (
    parse_repos,
    fetch_repo_info,
    fetch_open_issues,
    clone_repo,
    read_file_structure,
    read_key_files,
    analyze_repo_with_llm,
    create_plan,
    fork_and_prepare_repo,
    commit_and_push,
    create_pr,
)
from models.provider import GeminiProvider
from context import SessionContext


def parse_input_node(state: AgentState, ctx: SessionContext) -> dict:
    repos = parse_repos(state.raw_input)
    ctx.repos = repos
    ctx.add_message("user", state.raw_input)
    if not repos:
        return {"step": "awaiting_input", "error": "Could not parse repo names."}
    return {"repos": repos, "step": "parsed"}


def fetch_info_node(state: AgentState, ctx: SessionContext) -> dict:
    repo = state.selected_repo or state.repos[0]
    info = fetch_repo_info(repo)
    if "error" in info:
        return {"step": "error", "error": info["error"]}
    ctx.add_message("system", f"Fetched info for {repo}")
    return {"selected_repo": repo, "repo_analysis": str(info), "step": "info_fetched"}


def clone_and_analyze_node(state: AgentState, ctx: SessionContext) -> dict:
    repo = state.selected_repo
    llm = GeminiProvider(state.selected_model)

    issues = fetch_open_issues(repo)
    ctx.issues = issues

    repo_path = clone_repo(repo)
    structure = read_file_structure(repo_path)
    files = read_key_files(
        repo_path,
        ["*.py", "*.js", "*.ts", "*.rs", "*.go", "*.md", "*.json", "Cargo.toml", "package.json", "pyproject.toml"],
    )

    analysis = analyze_repo_with_llm(repo, structure, files, issues, llm)
    ctx.analysis = analysis
    ctx.add_message("system", f"Analysis complete for {repo}.")

    return {
        "issues_found": issues,
        "issue_details": "\n".join(f"#{i['number']}: {i['title']}" for i in issues[:5]),
        "step": "analyzed",
    }


def generate_plan_node(state: AgentState, ctx: SessionContext) -> dict:
    llm = GeminiProvider(state.selected_model)
    user_context = ctx.conversation_history()
    issue_num = state.selected_issue or (state.issues_found[0]["number"] if state.issues_found else 0)

    plan = create_plan(llm, state.selected_repo, state.repo_analysis, state.issues_found, issue_num, user_context)
    ctx.plan = plan
    ctx.add_message("system", "Plan generated.")

    return {"proposed_plan": plan, "step": "plan_ready"}


def plan_approved_node(state: AgentState, ctx: SessionContext) -> dict:
    ctx.user_feedback = state.user_feedback or ""
    ctx.add_message("user", state.user_feedback or "(no feedback)")
    if not state.plan_approved:
        return {"step": "user_rejected", "done": True}
    return {"step": "approved"}


def execute_changes_node(state: AgentState, ctx: SessionContext) -> dict:
    repo = state.selected_repo
    llm = GeminiProvider(state.selected_model)
    issue_num = state.selected_issue or (state.issues_found[0]["number"] if state.issues_found else 0)
    issue_title = state.issues_found[0]["title"] if state.issues_found else ""

    branch_name = f"fix-{issue_num}-{repo.split('/')[1]}"
    repo_path = clone_repo(repo)

    changes = fork_and_prepare_repo(repo, branch_name, llm, state.proposed_plan)
    ctx.add_message("system", f"Prepared changes for {branch_name}")

    title = f"Fix: #{issue_num} - {issue_title}" if issue_num else "Code improvements"
    commit_msg = f"Fix #{issue_num}: {issue_title}" if issue_num else "Apply code improvements"

    commit_and_push(repo_path, branch_name, commit_msg)

    pr_body = f"""## Description

{state.proposed_plan[:3000]}

## Related
Closes #{issue_num}

## Checklist
- [x] Code follows project conventions
- [x] Minimal, focused changes
"""

    pr_url = create_pr(repo, branch_name, title, pr_body)
    ctx.pr_urls.append(pr_url)
    ctx.add_message("system", f"PR created: {pr_url}")

    return {"branch_name": branch_name, "pr_title": title, "pr_url": pr_url, "step": "pr_created", "done": True}
