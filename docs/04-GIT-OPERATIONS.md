# OSS Contributor Agent v2 — Git Operations

## How Git/Fork/Push/PR Works

All git operations go through `git_ops.py`. The agent never runs raw `subprocess` commands directly.

---

## Architecture

```
┌─────────────────────────────────────────────┐
│  agent/tools.py                             │
│                                              │
│  clone_repo()           → git_ops.clone()    │
│  fork_and_prepare_repo() → git_ops.fork()    │
│                            git_ops.add_remote()│
│                            git_ops.fetch()   │
│                            git_ops.checkout() │
│  commit_and_push()       → git_ops.stage_all()│
│                            git_ops.commit()  │
│                            git_ops.push()    │
│  create_pr()             → git_ops.create_pr()│
└──────────────────┬──────────────────────────┘
                   │
                   ▼
┌─────────────────────────────────────────────┐
│  git_ops.py                                  │
│                                              │
│  Uses GITHUB_TOKEN for all auth              │
│  Wraps subprocess.run(["git", ...])          │
│  Wraps GitHubClient for API operations       │
└──────────────────┬──────────────────────────┘
                   │
          ┌────────┴────────┐
          ▼                 ▼
┌──────────────┐  ┌──────────────┐
│  git CLI     │  │  GitHub API  │
│  (local)     │  │  (PyGithub)  │
└──────────────┘  └──────────────┘
```

---

## Authentication

### Git Commands (clone, push, fetch)
Uses token in URL:
```
https://x-access-token:{GITHUB_TOKEN}@github.com/owner/repo.git
```

This is set per-remote, so `origin` and `fork` both have auth.

### GitHub API (fork, PR creation)
Uses PyGithub with token:
```python
Github(settings.GITHUB_TOKEN)
```

---

## Complete Fork → PR Flow

Here's exactly what happens when the agent creates a PR:

### Step 1: Clone the upstream repo
```python
git_ops.clone("owner/repo")
# Runs: git clone https://x-access-token:TOKEN@github.com/owner/repo.git workspace/owner_repo
# Returns: Path("workspace/owner_repo")
```

### Step 2: Fork the repo via API
```python
fork_name = git_ops.fork("owner/repo")
# Runs: GitHubClient.create_fork("owner/repo")
# Returns: "youruser/repo"
```

### Step 3: Add fork as remote
```python
git_ops.add_remote(repo_path, "fork", "youruser/repo")
# Runs: git remote add fork https://x-access-token:TOKEN@github.com/youruser/repo.git
```

### Step 4: Fetch fork remote
```python
git_ops.fetch(repo_path, "fork")
# Runs: git fetch fork
```

### Step 5: Create branch
```python
git_ops.checkout_new_branch(repo_path, "fix-issue-42")
# Runs: git checkout -b fix-issue-42
```

### Step 6: Write code changes
```python
write_changes(repo_path, changes_text)
# Parses FILE: markers from LLM output
# Writes files directly (no git commands)
```

### Step 7: Stage all changes
```python
git_ops.stage_all(repo_path)
# Runs: git add .
```

### Step 8: Commit
```python
git_ops.commit(repo_path, "Fix: resolve issue #42")
# Runs: git commit -m "Fix: resolve issue #42"
```

### Step 9: Push to fork
```python
git_ops.push(repo_path, "fork", "fix-issue-42")
# Runs: git push fork fix-issue-42
```

### Step 10: Create PR via API
```python
pr_url = git_ops.create_pr("owner/repo", "fix-issue-42", title, body)
# Runs: GitHubClient.create_pull_request(...)
# head = "youruser:fix-issue-42"
# base = "main" (default branch)
# Returns: "https://github.com/owner/repo/pull/123"
```

---

## Visual Flow

```
                    GitHub (remote)
                    ┌─────────────────────┐
                    │  owner/repo (upstream)│
                    │  ├── main            │
                    │  └── (your PR here)  │
                    └──────────┬──────────┘
                               │
              ┌────────────────┼────────────────┐
              │                                 │
              ▼                                 ▼
┌──────────────────────┐          ┌──────────────────────┐
│  youruser/repo (fork) │          │  Local workspace     │
│  ├── main             │          │  workspace/owner_repo│
│  └── fix-issue-42 ◄───┼──────────┤  ├── .git/           │
│       (pushed here)   │  push    │  ├── src/            │
└──────────────────────┘          │  ├── package.json    │
                                   │  └── (your changes)  │
                                   └──────────────────────┘
```

---

## Error Handling

### `GitError` Exception
All git operations raise `GitError` on failure:
```python
class GitError(Exception):
    pass

# Raised when:
# - git clone fails (auth, network)
# - git push fails (permissions, conflicts)
# - git commit fails (nothing to commit)
# - git checkout fails (branch exists)
```

### Common Errors:
| Error | Cause | Fix |
|-------|-------|-----|
| `401 Unauthorized` | Bad/expired GITHUB_TOKEN | Regenerate token |
| `403 Forbidden` | Token lacks `repo` scope | Add `repo` scope to token |
| `404 Not Found` | Repo doesn't exist | Check repo name |
| `nothing to commit` | No changes written | Check LLM output parsing |
| `failed to push` | Branch conflict | Force push or new branch |

---

## The `fork_branch_commit_push` Shortcut

Instead of calling 7 separate functions, use the high-level workflow:
```python
repo_path, fork_name = git_ops.fork_branch_commit_push(
    repo_full_name="owner/repo",
    branch_name="fix-issue-42",
    commit_message="Fix: resolve issue #42",
    repo_path=existing_path,  # or None to clone fresh
)
# Does: clone → fork → add_remote → fetch → checkout → add → commit → push
```

---

## Environment Variables

```bash
# .env
GITHUB_TOKEN=ghp_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
```

Required scopes for the token:
- `repo` — Full control of private repositories
- `workflow` — Update GitHub Actions (if needed)

For public repos only:
- `public_repo` — Access public repositories

---

## File Write Format

The LLM generates changes in this format:
```
FILE: src/components/Button.tsx
import React from 'react';

export const Button = ({ onClick, children }) => {
  return <button onClick={onClick}>{children}</button>;
};

FILE: src/styles/button.css
.button {
  padding: 8px 16px;
  border-radius: 4px;
}
```

`write_changes()` parses this and writes each file.
