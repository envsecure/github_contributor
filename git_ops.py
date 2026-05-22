"""
Git operations module.

All git/fork/push/PR operations go through here.
Uses GITHUB_TOKEN from environment for authentication.

Includes error recovery: when a git command fails, the agent analyzes
the error and takes corrective action (e.g., branch exists → use different name).
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path

from config import settings
from github_client.client import GitHubClient

gh = GitHubClient()

MAX_RETRIES = 3


class GitError(Exception):
    """Raised when a git operation fails after all retries."""
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


# ─── Error Analysis ───────────────────────────────────────────────────

def _analyze_error(error: GitError, context: dict) -> dict | None:
    """Analyze a GitError and return a recovery action, or None if unrecoverable.

    Returns:
        {"action": str, ...params} or None
    """
    msg = str(error).lower()

    # Branch already exists
    if "already exists" in msg and "branch" in msg:
        branch = context.get("branch_name", "")
        return {
            "action": "branch_exists",
            "branch": branch,
            "suggestion": f"Branch '{branch}' already exists. Use a different name or checkout existing.",
        }

    # Nothing to commit
    if "nothing to commit" in msg or "no changes added to commit" in msg:
        return {
            "action": "nothing_to_commit",
            "suggestion": "No changes were staged. Check if files were written correctly.",
        }

    # Push rejected (non-fast-forward)
    if "rejected" in msg or "non-fast-forward" in msg:
        return {
            "action": "push_rejected",
            "suggestion": "Push was rejected. Will force-push to fork.",
        }

    # Remote already exists
    if "remote" in msg and "already exists" in msg:
        remote = context.get("remote_name", "fork")
        return {
            "action": "remote_exists",
            "remote": remote,
            "suggestion": f"Remote '{remote}' already exists. Will use existing remote.",
        }

    # Authentication failed
    if "401" in msg or "403" in msg or "authentication" in msg or "permission" in msg:
        return {
            "action": "auth_failed",
            "suggestion": "Authentication failed. Check GITHUB_TOKEN.",
        }

    # Network error
    if "could not resolve" in msg or "connection" in msg or "timeout" in msg:
        return {
            "action": "network_error",
            "suggestion": "Network error. Will retry.",
        }

    # Repo not found
    if "404" in msg or "not found" in msg:
        return {
            "action": "not_found",
            "suggestion": "Repository not found. Check the repo name.",
        }

    return None


def _next_branch_name(branch: str) -> str:
    """Generate an alternative branch name by appending a suffix."""
    # If ends with number, increment it
    match = re.search(r'-(\d+)$', branch)
    if match:
        num = int(match.group(1))
        return branch[:match.start()] + f"-{num + 1}"
    return branch + "-v2"


# ─── Retry Wrapper ────────────────────────────────────────────────────

def _run_with_recovery(
    cmd: list[str],
    cwd: str | None = None,
    context: dict | None = None,
    max_retries: int = MAX_RETRIES,
) -> subprocess.CompletedProcess:
    """Run a git command with automatic error recovery and retry.

    On failure:
    1. Analyze the error
    2. If recoverable, take corrective action and retry
    3. If not recoverable, raise GitError
    """
    context = context or {}
    last_error = None

    for attempt in range(max_retries):
        try:
            return _run(cmd, cwd=cwd, check=True)
        except GitError as e:
            last_error = e
            recovery = _analyze_error(e, context)

            if recovery is None:
                # Unrecoverable — re-raise
                raise

            action = recovery["action"]

            # --- Branch exists → try alternative name ---
            if action == "branch_exists":
                new_branch = _next_branch_name(context.get("branch_name", "fix"))
                context["branch_name"] = new_branch
                # Update the command with new branch name
                cmd = _replace_branch_in_cmd(cmd, context.get("branch_name"), new_branch)
                continue

            # --- Remote exists → not an error, continue ---
            if action == "remote_exists":
                return subprocess.CompletedProcess(cmd, 0, "", "")

            # --- Push rejected → force push to fork ---
            if action == "push_rejected":
                cmd = _add_force_flag(cmd)
                continue

            # --- Nothing to commit → return success (empty commit is ok) ---
            if action == "nothing_to_commit":
                return subprocess.CompletedProcess(cmd, 0, "", "nothing to commit")

            # --- Network error → retry as-is ---
            if action == "network_error":
                continue

            # --- Auth/not-found → fatal, raise ---
            if action in ("auth_failed", "not_found"):
                raise

    # All retries exhausted
    raise last_error


def _replace_branch_in_cmd(cmd: list[str], old_branch: str, new_branch: str) -> list[str]:
    """Replace branch name in a git command."""
    return [new_branch if arg == old_branch else arg for arg in cmd]


def _add_force_flag(cmd: list[str]) -> list[str]:
    """Add --force-with-lease to a push command."""
    if "push" in cmd:
        idx = cmd.index("push")
        # Insert --force-with-lease after "push"
        return cmd[:idx + 1] + ["--force-with-lease"] + cmd[idx + 1:]
    return cmd


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
    _run_with_recovery(
        ["git", "remote", "add", name, url],
        cwd=str(repo_path),
        context={"remote_name": name},
    )


def fetch(repo_path: Path, remote: str = "origin") -> None:
    """Fetch from a remote."""
    _run_with_recovery(["git", "fetch", remote], cwd=str(repo_path))


def checkout_new_branch(repo_path: Path, branch_name: str) -> str:
    """Create and checkout a new branch.

    If the branch already exists, generates an alternative name and uses that.

    Returns:
        The actual branch name used (may differ from input if recovery kicked in).
    """
    ctx = {"branch_name": branch_name}
    try:
        _run_with_recovery(
            ["git", "checkout", "-b", branch_name],
            cwd=str(repo_path),
            context=ctx,
        )
        return ctx.get("branch_name", branch_name)
    except GitError:
        # Final fallback: checkout the existing branch instead of creating
        _run(["git", "checkout", branch_name], cwd=str(repo_path))
        return branch_name


def stage_all(repo_path: Path) -> None:
    """Stage all changes (git add .)."""
    _run(["git", "add", "."], cwd=str(repo_path))


def commit(repo_path: Path, message: str) -> None:
    """Commit staged changes. Handles 'nothing to commit' gracefully."""
    _run_with_recovery(
        ["git", "commit", "-m", message],
        cwd=str(repo_path),
        context={},
    )


def push(repo_path: Path, remote: str, branch: str) -> None:
    """Push a branch to a remote. Auto-retries with --force-with-lease on rejection."""
    _run_with_recovery(
        ["git", "push", remote, branch],
        cwd=str(repo_path),
        context={"branch_name": branch, "remote_name": remote},
    )


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
) -> tuple[Path, str, str]:
    """Full workflow: fork repo, create branch, stage+commit+push.

    Handles errors automatically:
    - Branch exists → uses alternative name
    - Remote exists → uses existing remote
    - Push rejected → force-pushes to fork
    - Nothing to commit → skips commit

    Args:
        repo_full_name: e.g. "owner/repo"
        branch_name: new branch name
        commit_message: commit message
        repo_path: local repo path (clones if not provided)

    Returns:
        (repo_path, fork_full_name, actual_branch_name)
    """
    # 1. Clone if needed
    if repo_path is None:
        repo_path = clone(repo_full_name)

    # 2. Fork
    fork_name = fork(repo_full_name)

    # 3. Add fork as remote and fetch (handles "remote already exists")
    add_remote(repo_path, "fork", fork_name)
    fetch(repo_path, "fork")

    # 4. Create branch (handles "branch already exists")
    actual_branch = checkout_new_branch(repo_path, branch_name)

    # 5. Stage, commit, push (handles "nothing to commit", "push rejected")
    stage_all(repo_path)
    commit(repo_path, commit_message)
    push(repo_path, "fork", actual_branch)

    return repo_path, fork_name, actual_branch


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
