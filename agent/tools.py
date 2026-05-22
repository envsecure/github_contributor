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


def fetch_open_issues(repo_name: str, limit: int = 5) -> list[dict]:
    issues = gh.get_open_issues(repo_name)
    results = []
    for issue in issues[:limit]:
        if not issue.pull_request:
            results.append({
                "number": issue.number,
                "title": issue.title,
                "body": (issue.body or "")[:500],
                "labels": [l.name for l in issue.labels],
            })
    return results


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


def read_file_structure(repo_path: Path, max_depth: int = 3) -> str:
    lines = []
    prefix = repo_path
    for p in sorted(repo_path.rglob("*")):
        if p.is_dir() or p.name.startswith(".") or p.name == "__pycache__":
            continue
        try:
            rel = p.relative_to(prefix)
            if len(rel.parts) <= max_depth:
                lines.append(str(rel))
        except ValueError:
            pass
    return "\n".join(lines[:80])


def read_key_files(repo_path: Path, patterns: list[str]) -> dict[str, str]:
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
    issues_text = "\n".join(
        f"  #{i['number']} - {i['title']} [{', '.join(i['labels'])}]"
        for i in issues
    ) or "  (no open issues)"

    files_text = "\n".join(
        f"  {path}:\n{content[:800]}\n"
        for path, content in list(files.items())[:5]
    )

    prompt = f"""You are analyzing the repository **{repo_name}**.

## File Structure
{structure[:1500]}

## Key Files
{files_text[:3000]}

## Open Issues
{issues_text[:1500]}

Your task:
1. Understand what this project does.
2. Identify which open issues are good candidates for a new contributor.
3. Look for potential bugs, code smells, or improvements beyond filed issues.
4. Summarize your findings."""

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
