from __future__ import annotations

import subprocess
from pathlib import Path

from langchain_core.messages import SystemMessage, HumanMessage

from config import settings
from models.provider import GeminiProvider
from github_client.client import GitHubClient

gh = GitHubClient()


def parse_repos(raw: str) -> list[str]:
    parts = [p.strip() for p in raw.replace(",", "\n").split("\n") if p.strip()]
    resolved = []
    for p in parts:
        if p.startswith("http"):
            if "github.com" in p:
                segments = p.rstrip("/").split("/")
                if len(segments) >= 2:
                    resolved.append("/".join(segments[-2:]))
        elif "/" in p and len(p.split("/")) == 2:
            resolved.append(p)
        else:
            resolved.append(p)
    return resolved


def fetch_repo_info(repo_name: str) -> dict:
    try:
        repo = gh.get_repo(repo_name)
        return {
            "name": repo.full_name,
            "description": repo.description or "",
            "language": repo.language or "",
            "stars": repo.stargazers_count,
            "open_issues": repo.open_issues_count,
            "topics": repo.get_topics(),
            "default_branch": repo.default_branch,
        }
    except Exception as e:
        return {"name": repo_name, "error": str(e)}


def fetch_open_issues(repo_name: str, limit: int = 50, recent_days: int = 2) -> list[dict]:
    """Fetch recent open issues (not PRs), filtered to last N days, up to limit."""
    issues = gh.get_open_issues(repo_name, limit=limit, recent_days=recent_days)
    results = []
    for issue in issues:
        results.append({
            "number": issue.number,
            "title": issue.title,
            "body": (issue.body or "")[:500],
            "labels": [l.name for l in issue.labels],
            "comments": issue.comments,
            "created_at": issue.created_at.isoformat() if issue.created_at else "",
        })
    return results


def detect_high_value_issues(issues: list[dict], llm: GeminiProvider, repo_name: str) -> list[dict]:
    """Use AI to rank issues by value for a contributor. Returns top issues."""
    if not issues:
        return []

    issues_text = "\n".join(
        f"#{i['number']} | {i['title']} | labels: {', '.join(i['labels'])} | comments: {i['comments']}"
        for i in issues[:50]
    )

    prompt = f"""You are analyzing open issues for **{repo_name}** to find the best ones for an OSS contributor.

Here are the recent open issues (last 2 days):
{issues_text}

Pick the TOP 5 most valuable issues for a contributor to work on. Consider:
- Issues with "good first issue", "help wanted", "bug" labels (high value)
- Issues with clear descriptions and moderate complexity
- Issues that are not too broad or vague
- Bugs over feature requests for new contributors

Return ONLY the issue numbers, one per line, nothing else. Example:
42
107
233"""

    msg = llm.invoke([
        SystemMessage(content="You are an OSS contribution advisor. Return only issue numbers."),
        HumanMessage(content=prompt),
    ])

    # Parse the numbers from AI response
    top_numbers = set()
    for line in msg.strip().split("\n"):
        line = line.strip().lstrip("#")
        if line.isdigit():
            top_numbers.add(int(line))

    # Return matching issues in original order
    ranked = [i for i in issues if i["number"] in top_numbers]
    # If AI didn't return valid numbers, fall back to label-based ranking
    if not ranked:
        priority_labels = {"good first issue", "help wanted", "bug", "enhancement", "documentation"}
        ranked = sorted(
            issues,
            key=lambda i: sum(1 for l in i["labels"] if l.lower() in priority_labels),
            reverse=True,
        )
    return ranked[:10]


def clone_repo(repo_name: str) -> Path:
    namespace = repo_name.replace("/", "_")
    dest = settings.WORK_DIR / namespace
    if dest.exists():
        return dest
    url = f"https://github.com/{repo_name}.git"
    subprocess.run(
        ["git", "clone", url, str(dest)],
        check=True, capture_output=True, text=True,
    )
    return dest


def read_file_structure(repo_path: Path, max_depth: int = 3, max_files: int = 200) -> str:
    """Read the file tree structure, up to max_files entries."""
    lines = []
    for p in sorted(repo_path.rglob("*")):
        if p.is_dir() or p.name.startswith(".") or p.name == "__pycache__" or p.name == "node_modules":
            continue
        try:
            rel = p.relative_to(repo_path)
            if len(rel.parts) <= max_depth:
                lines.append(str(rel))
        except ValueError:
            pass
    return "\n".join(lines[:max_files])


def search_code(repo_path: Path, query: str, max_results: int = 30) -> list[dict]:
    """Search code content in the repo (grep-style). Returns matching file paths and line snippets."""
    results = []
    for p in repo_path.rglob("*"):
        if not p.is_file() or p.name.startswith(".") or p.name in {"node_modules", "__pycache__", ".git"}:
            continue
        if p.stat().st_size > 500_000:
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
            for i, line in enumerate(text.splitlines(), 1):
                if query.lower() in line.lower():
                    rel = str(p.relative_to(repo_path))
                    results.append({
                        "file": rel,
                        "line": i,
                        "content": line.strip()[:200],
                    })
                    if len(results) >= max_results:
                        return results
        except Exception:
            pass
    return results


def glob_files(repo_path: Path, pattern: str, max_results: int = 50) -> list[str]:
    """Find files matching a glob pattern relative to repo_path."""
    results = []
    for p in repo_path.glob(pattern):
        if p.is_file():
            rel = str(p.relative_to(repo_path))
            results.append(rel)
            if len(results) >= max_results:
                break
    return results


def read_file_chunked(repo_path: Path, file_path: str, chunk_size: int = 4000, chunk_index: int = 0) -> dict:
    """Read a file in chunks. Returns a dict with path, content, total_size, chunk info."""
    full_path = repo_path / file_path
    if not full_path.exists() or not full_path.is_file():
        return {"path": file_path, "content": "", "total_size": 0, "chunks": 0, "chunk": 0, "error": "File not found"}

    try:
        text = full_path.read_text(encoding="utf-8", errors="ignore")
    except Exception as e:
        return {"path": file_path, "content": "", "total_size": 0, "chunks": 0, "chunk": 0, "error": str(e)}

    total_size = len(text)
    chunks = max(1, (total_size + chunk_size - 1) // chunk_size)
    start = chunk_index * chunk_size
    end = start + chunk_size
    content = text[start:end]

    return {
        "path": file_path,
        "content": content,
        "total_size": total_size,
        "chunks": chunks,
        "chunk": chunk_index,
    }


def select_relevant_files(repo_path: Path, issues: list[dict], llm: GeminiProvider, console=None) -> list[str]:
    """AI selects which files to read based on the file tree and issues. OpenCode-style."""
    tree = read_file_structure(repo_path, max_depth=4, max_files=300)
    if not tree:
        return []

    issues_text = "\n".join(
        f"#{i['number']}: {i['title']} [{', '.join(i['labels'])}]"
        for i in issues[:10]
    ) or "(no issues)"

    # Also do keyword search for relevant terms from issue titles
    search_hits = []
    for issue in issues[:5]:
        words = [w for w in issue["title"].split() if len(w) > 3]
        for word in words[:3]:
            hits = search_code(repo_path, word, max_results=5)
            search_hits.extend(hits)
    # Deduplicate files from search
    searched_files = list(dict.fromkeys(h["file"] for h in search_hits))[:20]
    search_context = "\n".join(f"  {f}" for f in searched_files) if searched_files else "(none)"

    prompt = f"""You are analyzing repository code to find files relevant to fixing these issues.

## File Tree
{tree[:4000]}

## Issues to Fix
{issues_text}

## Files Found by Keyword Search
{search_context}

Your task: Select the 5-10 most relevant files to read in order to understand and fix these issues.
Consider:
- Source files mentioned in issue titles or descriptions
- Configuration files that might be related
- Test files for the affected code
- Entry points and main modules

Return ONLY file paths, one per line, nothing else. Example:
src/schema.ts
packages/core/src/index.ts
tests/schema.test.ts"""

    if console:
        console.print("  [dim]  AI selecting relevant files...[/dim]")

    msg = llm.invoke([
        SystemMessage(content="You are a code analysis agent. Return only file paths, one per line."),
        HumanMessage(content=prompt),
    ])

    # Parse file paths from response
    selected = []
    for line in msg.strip().split("\n"):
        line = line.strip().strip("`").strip()
        if not line or line.startswith("#") or line.startswith("-") or line.startswith("*"):
            continue
        # Clean up markdown formatting
        line = line.lstrip("0123456789). ").strip()
        if line and (repo_path / line).exists():
            selected.append(line)

    # Fallback: if AI didn't return valid files, use search results
    if not selected:
        selected = searched_files[:10]

    # Fallback: if still empty, use pattern-based selection
    if not selected:
        selected = read_key_files(repo_path, ["*.py", "*.js", "*.ts", "*.rs", "*.go"])
        selected = list(selected.keys())[:10]

    return selected[:10]


def read_key_files(repo_path: Path, patterns: list[str]) -> dict[str, str]:
    """Fallback: read files matching hardcoded patterns."""
    result = {}
    for p in repo_path.rglob("*"):
        if p.is_file() and any(p.name == pat or p.match(pat) for pat in patterns):
            try:
                content = p.read_text(encoding="utf-8", errors="ignore")[:3000]
                result[str(p.relative_to(repo_path))] = content
            except Exception:
                pass
    return result


def analyze_repo_with_llm(repo_name: str, structure: str, files: dict, issues: list[dict], llm: GeminiProvider) -> str:
    """Analyze the repo with the LLM using discovered file contents."""
    issues_text = "\n".join(
        f"  #{i['number']} - {i['title']} [{', '.join(i['labels'])}]"
        for i in issues
    ) or "  (no open issues)"

    files_text = "\n\n".join(
        f"--- {path} ({len(content)} chars) ---\n{content}"
        for path, content in files.items()
    )

    prompt = f"""You are analyzing the repository **{repo_name}**.

## File Structure
{structure[:2000]}

## Selected Files
{files_text[:8000]}

## Open Issues
{issues_text[:2000]}

Your task:
1. Understand what this project does.
2. Identify which open issues are good candidates for a new contributor.
3. Look for potential bugs, code smells, or improvements beyond filed issues.
4. For each issue, note which files are relevant and why.
5. Summarize your findings."""

    msg = llm.invoke([
        SystemMessage(content="You are a thorough code reviewer analyzing a GitHub repository."),
        HumanMessage(content=prompt),
    ])
    return msg


def create_plan(llm: GeminiProvider, repo: str, analysis: str, issues: list[dict], issue_num: int, user_context: str) -> str:
    target = next((i for i in issues if i["number"] == issue_num), issues[0] if issues else None)
    issue_text = f"#{target['number']}: {target['title']}\n{target['body'][:1000]}" if target else "No specific issue targeted."

    prompt = f"""Based on the following analysis, create a detailed implementation plan.

### Repository
{repo}

### Issue Target
{issue_text}

### Analysis Context
{analysis[:2000]}

### User Context
{user_context[:1000]}

### Plan Requirements
- Which files to modify and how
- Exact code changes (be specific)
- Any new tests needed
- Commit message
- Risks or edge cases

Output a structured, step-by-step plan."""

    msg = llm.invoke([
        SystemMessage(content="You are a senior software engineer creating an implementation plan. Be precise and actionable."),
        HumanMessage(content=prompt),
    ])
    return msg


def fork_and_prepare_repo(repo_name: str, branch_name: str, llm: GeminiProvider, plan: str) -> str:
    user = gh.get_user()
    fork = gh.create_fork(repo_name)
    fork_full = f"{user}/{repo_name.split('/')[1]}"

    namespace = repo_name.replace("/", "_")
    repo_path = settings.WORK_DIR / namespace

    if not repo_path.exists():
        clone_repo(repo_name)

    fork_url = f"https://{user}:{settings.GITHUB_TOKEN}@github.com/{fork_full}.git"
    subprocess.run(["git", "remote", "add", "fork", fork_url], cwd=str(repo_path), capture_output=True)
    subprocess.run(["git", "fetch", "fork"], cwd=str(repo_path), capture_output=True)
    subprocess.run(["git", "checkout", "-b", branch_name], cwd=str(repo_path), check=True, capture_output=True)

    prompt = f"""The repository is cloned at {repo_path}. Here is the plan:

{plan}

Read the relevant files and output the exact changes needed. For each file, show:
- File path
- What to add/remove
- The exact new content or diff"""

    msg = llm.invoke([
        SystemMessage(content="You are implementing changes. Output specific file modifications."),
        HumanMessage(content=prompt),
    ])
    return msg


def write_changes(repo_path: Path, changes_text: str) -> str:
    log = []
    current_file = None
    current_content = []

    for line in changes_text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("FILE:"):
            if current_file and current_content:
                file_path = repo_path / current_file
                file_path.parent.mkdir(parents=True, exist_ok=True)
                file_path.write_text("\n".join(current_content), encoding="utf-8")
                log.append(f"Written: {current_file}")
            current_file = stripped.split(":", 1)[1].strip()
            current_content = []
        elif current_file is not None:
            current_content.append(line)

    if current_file and current_content:
        file_path = repo_path / current_file
        file_path.parent.mkdir(parents=True, exist_ok=True)
        file_path.write_text("\n".join(current_content), encoding="utf-8")
        log.append(f"Written: {current_file}")

    return "\n".join(log) if log else "No files to write."


def commit_and_push(repo_path: Path, branch_name: str, commit_msg: str) -> None:
    subprocess.run(["git", "add", "."], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", commit_msg], cwd=str(repo_path), check=True, capture_output=True)
    subprocess.run(["git", "push", "fork", branch_name], cwd=str(repo_path), check=True, capture_output=True)


def create_pr(repo_name: str, branch_name: str, title: str, body: str) -> str:
    user = gh.get_user()
    fork_repo = repo_name.split("/")[1]
    head = f"{user}:{branch_name}"
    repo = gh.get_repo(repo_name)
    base = repo.default_branch
    pr = gh.create_pull_request(repo_name, head=head, base=base, title=title, body=body)
    return pr.html_url
