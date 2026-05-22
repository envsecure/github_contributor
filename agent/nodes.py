from __future__ import annotations

from agent.state import AgentState
from agent.tools import (
    parse_repos,
    fetch_repo_info,
    fetch_open_issues,
    detect_high_value_issues,
    clone_repo,
    read_file_structure,
    search_code,
    select_relevant_files,
    read_file_chunked,
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
    from rich.console import Console
    console = Console()

    repo = state.selected_repo
    llm = GeminiProvider(state.selected_model)

    # --- Step 1: Clone repo first (so we can search it) ---
    console.print("  [dim] 5%[/dim] Cloning repository...")
    repo_path = clone_repo(repo)

    # --- Step 2: Read file tree ---
    console.print("  [dim]10%[/dim] Reading file structure...")
    structure = read_file_structure(repo_path, max_depth=4, max_files=300)
    file_count = len(structure.splitlines()) if structure else 0
    console.print(f"  [dim]12%[/dim] Found {file_count} files in tree.")

    # --- Step 3: Fetch recent issues ---
    console.print("  [dim]15%[/dim] Fetching recent open issues (last 2 days, max 50)...")
    issues = fetch_open_issues(repo, limit=50, recent_days=2)
    console.print(f"  [dim]20%[/dim] Found {len(issues)} issues. Filtering PRs...")

    # --- Step 4: AI high-value issue detection ---
    if issues:
        console.print("  [dim]25%[/dim] AI detecting high-value issues...")
        issues = detect_high_value_issues(issues, llm, repo)
        console.print(f"  [dim]30%[/dim] Selected {len(issues)} high-value issues.")
    else:
        console.print("  [dim]25%[/dim] No recent issues found. Will analyze code only.")

    ctx.issues = issues

    # --- Step 5: AI selects which files to read (OpenCode-style) ---
    console.print("  [dim]35%[/dim] Searching code for relevant patterns...")
    selected_files = select_relevant_files(repo_path, issues, llm, console)
    console.print(f"  [dim]45%[/dim] Selected {len(selected_files)} files to read:")
    for f in selected_files:
        console.print(f"  [dim]       → {f}[/dim]")

    # --- Step 6: Read selected files in chunks ---
    file_contents = {}
    total = len(selected_files) or 1
    for idx, file_path in enumerate(selected_files):
        pct = 50 + int(30 * (idx / total))
        chunk = read_file_chunked(repo_path, file_path, chunk_size=4000, chunk_index=0)
        size = chunk["total_size"]
        chunks = chunk["chunks"]
        if chunk.get("error"):
            console.print(f"  [dim]{pct}%[/dim] ✗ {file_path}: {chunk['error']}")
            continue

        if chunks > 1:
            console.print(f"  [dim]{pct}%[/dim] Reading {file_path} ({size:,} chars, chunk 1/{chunks})...")
            # Read remaining chunks
            content = chunk["content"]
            for ci in range(1, min(chunks, 3)):  # max 3 chunks per file
                extra = read_file_chunked(repo_path, file_path, chunk_size=4000, chunk_index=ci)
                content += "\n" + extra["content"]
            file_contents[file_path] = content
        else:
            console.print(f"  [dim]{pct}%[/dim] Reading {file_path} ({size:,} chars, complete)")
            file_contents[file_path] = chunk["content"]

    ctx.file_contents = file_contents

    # --- Step 7: LLM analysis ---
    console.print("  [dim]85%[/dim] Analyzing code with AI...")
    analysis = analyze_repo_with_llm(repo, structure, file_contents, issues, llm)
    ctx.analysis = analysis
    console.print("  [dim]100%[/dim] Analysis complete.")

    ctx.add_message("system", f"Analysis complete for {repo}.")

    return {
        "issues_found": issues,
        "issue_details": "\n".join(f"#{i['number']}: {i['title']}" for i in issues[:5]),
        "selected_files": selected_files,
        "file_contents": file_contents,
        "step": "analyzed",
    }


def generate_plan_node(state: AgentState, ctx: SessionContext) -> dict:
    from rich.console import Console
    console = Console()

    llm = GeminiProvider(state.selected_model)
    user_context = ctx.conversation_history()
    issue_num = state.selected_issue or (state.issues_found[0]["number"] if state.issues_found else 0)

    console.print("  [dim]Generating implementation plan...[/dim]")
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
