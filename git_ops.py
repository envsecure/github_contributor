"""
Git operations module.

All git/fork/push/PR operations go through here.
Uses GITHUB_TOKEN from environment for authentication.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

from config import settings
from github_client.client import GitHubClient

gh = GitHubClient()


class GitError(Exception):
    """Raised when a git operation fails."""
    pass


def _run(cmd: list[str], cwd: str | None = None, check: bool = True) -> subprocess.CompletedProcess:
    """Run a git command and return the result."""
    result = subprocess.run(
        cmd,
        cwd=cwd,
        capture_output=True,
        text=True,
    )
    if check and result.returncode != 0:
        raise GitError(f"{' '.join(cmd)}\n  stderr: {result.stderr.strip()}")
    return result


# ─── Repository Operations ────────────────────────────────────────────

def clone(repo_full_name: str, dest: Path | None = None) -> Path:
    """Clone a repository using GITHUB_TOKEN for auth.

    Args:
        repo_full_name: e.g. "owner/repo"
        dest: destination path (defaults to WORK_DIR / namespace)

    Returns:
        Path to the cloned repository.
    """
    namespace = repo_full_name.replace("/", "_")
    dest = dest or (settings.WORK_DIR / namespace)

    if dest.exists() and (dest / ".git").exists():
        return dest

    url = f"https://x-access-token:{settings.GITHUB_TOKEN}@github.com/{repo_full_name}.git"
    _run(["git", "clone", url, str(dest)])
    return dest


def fork(repo_full_name: str) -> str:
    """Fork a repository via GitHub API.

    Returns:
        The fork's full name (e.g. "youruser/repo").
    """
    fork_repo = gh.create_fork(repo_full_name)
    return fork_repo.full_name


def add_remote(repo_path: Path, name: str, repo_full_name: str) -> None:
    """Add a remote to the local repo, using GITHUB_TOKEN auth.

    Args:
        repo_path: local repo path
        name: remote name (e.g. "fork")
        repo_full_name: e.g. "youruser/repo"
    """
    url = f"https://x-access-token:{settings.GITHUB_TOKEN}@github.com/{repo_full_name}.git"
    _run(["git", "remote", "add", name, url], cwd=str(repo_path), check=False)


def fetch(repo_path: Path, remote: str = "origin") -> None:
    """Fetch from a remote."""
    _run(["git", "fetch", remote], cwd=str(repo_path))


def checkout_new_branch(repo_path: Path, branch_name: str) -> None:
    """Create and checkout a new branch."""
    _run(["git", "checkout", "-b", branch_name], cwd=str(repo_path))


def stage_all(repo_path: Path) -> None:
    """Stage all changes (git add .)."""
    _run(["git", "add", "."], cwd=str(repo_path))


def commit(repo_path: Path, message: str) -> None:
    """Commit staged changes."""
    _run(["git", "commit", "-m", message], cwd=str(repo_path))


def push(repo_path: Path, remote: str, branch: str) -> None:
    """Push a branch to a remote."""
    _run(["git", "push", remote, branch], cwd=str(repo_path))


def get_current_branch(repo_path: Path) -> str:
    """Return the current branch name."""
    result = _run(["git", "rev-parse", "--abbrev-ref", "HEAD"], cwd=str(repo_path))
    return result.stdout.strip()


def get_default_branch(repo_full_name: str) -> str:
    """Return the default branch of a remote repo."""
    repo = gh.get_repo(repo_full_name)
    return repo.default_branch


# ─── Pull Request Operations ──────────────────────────────────────────

def create_pull_request(
    repo_full_name: str,
    head: str,
    base: str,
    title: str,
    body: str,
) -> str:
    """Create a pull request via GitHub API.

    Args:
        repo_full_name: e.g. "owner/repo"
        head: e.g. "youruser:branch-name"
        base: target branch (e.g. "main")
        title: PR title
        body: PR body (markdown)

    Returns:
        The PR URL.
    """
    pr = gh.create_pull_request(repo_full_name, head=head, base=base, title=title, body=body)
    return pr.html_url


# ─── High-Level Workflows ─────────────────────────────────────────────

def fork_branch_commit_push(
    repo_full_name: str,
    branch_name: str,
    commit_message: str,
    repo_path: Path | None = None,
) -> tuple[Path, str]:
    """Full workflow: fork repo, create branch, stage+commit+push.

    Args:
        repo_full_name: e.g. "owner/repo"
        branch_name: new branch name
        commit_message: commit message
        repo_path: local repo path (clones if not provided)

    Returns:
        (repo_path, fork_full_name)
    """
    # 1. Clone if needed
    if repo_path is None:
        repo_path = clone(repo_full_name)

    # 2. Fork
    fork_name = fork(repo_full_name)

    # 3. Add fork as remote and fetch
    add_remote(repo_path, "fork", fork_name)
    fetch(repo_path, "fork")

    # 4. Create branch
    checkout_new_branch(repo_path, branch_name)

    # 5. Stage, commit, push
    stage_all(repo_path)
    commit(repo_path, commit_message)
    push(repo_path, "fork", branch_name)

    return repo_path, fork_name


def create_pr(
    repo_full_name: str,
    branch_name: str,
    title: str,
    body: str,
    fork_name: str | None = None,
) -> str:
    """Create a PR from a fork branch to the upstream default branch.

    Args:
        repo_full_name: upstream repo (e.g. "owner/repo")
        branch_name: branch in the fork
        title: PR title
        body: PR body
        fork_name: fork repo full name (auto-detected if not provided)

    Returns:
        PR URL.
    """
    if fork_name is None:
        fork_name = f"{gh.get_user()}/{repo_full_name.split('/')[1]}"

    user = gh.get_user()
    head = f"{user}:{branch_name}"
    base = get_default_branch(repo_full_name)

    return create_pull_request(repo_full_name, head=head, base=base, title=title, body=body)
