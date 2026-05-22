# OSS Contributor Agent v2 — Code Reference

## File-by-File Guide

This document explains every file in the project, what it does, and how the pieces connect.

---

## `main.py` — Entry Point

```python
from cli import main

if __name__ == "__main__":
    main()
```

Just delegates to `cli.main()`.

---

## `config.py` — Settings

```python
class Settings:
    GITHUB_TOKEN: str       # From .env — GitHub API auth
    GEMINI_API_KEY: str     # From .env — Google AI auth
    LLM_MODEL: str          # Default: "gemma-3-27b-it"
    WORK_DIR: Path          # Default: "./workspace"
    MAX_STEPS: int          # Default: 30 (LangGraph recursion limit)
```

Loads from `.env` via `python-dotenv`. Creates `WORK_DIR` on import.

---

## `context.py` — Session Context

```python
class SessionContext:
    repos: list[str]         # User-provided repo names
    issues: list[dict]       # Fetched issues
    analysis: str            # LLM analysis output
    plan: str                # Generated plan
    user_feedback: str       # User edits to plan
    pr_urls: list[str]       # Created PR URLs
    messages: list[dict]     # Conversation history
    current_step: str        # Current step name
```

Shared mutable state. Passed to every graph node via lambda closures in `graph.py`.

Key methods:
- `add_message(role, content)` — Append to history
- `conversation_history()` — Last 20 messages as formatted string
- `reset()` — Clear all state

---

## `models/provider.py` — LLM Provider

```python
class GeminiProvider:
    def __init__(self, model: str | None = None)
    def invoke(self, messages: list[BaseMessage]) -> str
    def invoke_verbose(self, messages, label) -> str
    def list_models() -> list[str]          # @staticmethod
```

**Critical detail**: `invoke()` coerces `.content` to `str`. LangChain's `ChatGoogleGenerativeAI` can return a `list` of content blocks — this method joins them into a single string.

```
messages (list[BaseMessage])
    │
    ▼
ChatGoogleGenerativeAI.invoke(messages)
    │
    ▼
.content → str | list[dict]
    │
    ▼
invoke() → always str
```

---

## `github_client/client.py` — GitHub API Client

```python
class GitHubClient:
    def __init__(self)                      # Uses GITHUB_TOKEN
    def get_user(self) -> str               # Current user login
    def get_repo(self, name) -> Repository
    def search_issues(self, query, limit)
    def get_open_issues(self, repo, state, limit, recent_days)
    def create_fork(self, repo) -> Repository
    def create_pull_request(self, repo, head, base, title, body)
```

**`get_open_issues`** is the key method:
```python
def get_open_issues(self, repo_full_name, state="open", limit=50, recent_days=2):
    since = datetime.now(timezone.utc) - timedelta(days=recent_days)
    issues = repo.get_issues(state=state, sort="created", direction="desc", since=since)
    # Filters out PRs (GitHub returns PRs as issues too)
    # Returns up to `limit` issues
```

---

## `git_ops.py` — Git Operations

Centralized module for all git commands. Never use `subprocess` directly in agent code.

### Low-level functions:
```python
clone(repo_full_name, dest) → Path        # git clone with token auth
fork(repo_full_name) → str                # Fork via GitHub API
add_remote(repo_path, name, repo_full)     # git remote add
fetch(repo_path, remote)                   # git fetch
checkout_new_branch(repo_path, name)       # git checkout -b
stage_all(repo_path)                       # git add .
commit(repo_path, message)                 # git commit -m
push(repo_path, remote, branch)            # git push
get_current_branch(repo_path) → str
get_default_branch(repo_full_name) → str
create_pull_request(repo, head, base, title, body) → str  # PR via API
```

### High-level workflow:
```python
fork_branch_commit_push(repo, branch, msg, repo_path) → (Path, str)
create_pr(repo, branch, title, body, fork_name) → str
```

### Authentication:
All git URLs use token auth:
```
https://x-access-token:{GITHUB_TOKEN}@github.com/owner/repo.git
```

---

## `agent/state.py` — LangGraph State

```python
@dataclass
class AgentState:
    # User input
    raw_input: str = ""
    repos: list[str] = []
    selected_model: str = settings.LLM_MODEL
    selected_repo: str = ""
    selected_issue: int = 0

    # Analysis
    repo_analysis: str = ""
    issues_found: list[dict] = []
    issue_details: str = ""
    selected_files: list[str] = []
    search_results: str = ""
    file_contents: dict[str, str] = {}

    # Planning
    proposed_plan: str = ""
    plan_approved: bool = False
    user_feedback: str = ""

    # Execution
    branch_name: str = ""
    pr_title: str = ""
    pr_body: str = ""
    pr_url: str = ""
    changes_made: str = ""
    error: str | None = None

    # Routing
    step: str = "awaiting_input"
    done: bool = False
```

The `step` field controls graph routing. Each node sets `step` to indicate the next step.

---

## `agent/graph.py` — LangGraph Definitions

### Analysis Graph:
```python
def build_analysis_graph(ctx: SessionContext) -> StateGraph:
    # Nodes
    parse_input      → parse_input_node
    fetch_info       → fetch_info_node
    clone_and_analyze → clone_and_analyze_node
    generate_plan    → generate_plan_node

    # Routing
    parsed        → fetch_info
    info_fetched  → clone_and_analyze
    analyzed      → generate_plan
    plan_ready    → END
    error/done    → END
```

### Execution Graph:
```python
def build_execution_graph(ctx: SessionContext) -> StateGraph:
    # Nodes
    plan_approved   → plan_approved_node
    execute_changes → execute_changes_node

    # Routing
    approved → execute_changes
    else     → END
```

---

## `agent/nodes.py` — Graph Node Functions

### `parse_input_node(state, ctx)`
Parses raw input into repo names. Sets `state.repos`.

### `fetch_info_node(state, ctx)`
Gets repo metadata from GitHub API. Sets `state.repo_analysis`.

### `clone_and_analyze_node(state, ctx)`
The main analysis step. OpenCode-style file discovery:

```python
# 1. Clone
repo_path = git_ops.clone(repo)

# 2. Read tree
structure = read_file_structure(repo_path, max_depth=4, max_files=300)

# 3. Fetch issues
issues = fetch_open_issues(repo, limit=50, recent_days=2)

# 4. AI high-value detection
issues = detect_high_value_issues(issues, llm, repo)

# 5. AI file selection
selected_files = select_relevant_files(repo_path, issues, llm)

# 6. Read files in chunks
for file_path in selected_files:
    chunk = read_file_chunked(repo_path, file_path, chunk_size=4000)
    file_contents[file_path] = chunk["content"]

# 7. LLM analysis
analysis = analyze_repo_with_llm(repo, structure, file_contents, issues, llm)
```

### `generate_plan_node(state, ctx)`
Sends analysis + issue to LLM, gets implementation plan.

### `plan_approved_node(state, ctx)`
Generates branch name, PR title, PR body from the plan.

### `execute_changes_node(state, ctx)`
Full execution: fork → clone → branch → generate code → write → commit → push → PR.

---

## `agent/tools.py` — Tool Functions

### Issue Functions:
```python
fetch_open_issues(repo, limit=50, recent_days=2) → list[dict]
detect_high_value_issues(issues, llm, repo) → list[dict]
```

### File Discovery Functions:
```python
read_file_structure(repo_path, max_depth=3, max_files=200) → str
search_code(repo_path, query, max_results=30) → list[dict]
glob_files(repo_path, pattern, max_results=50) → list[str]
read_file_chunked(repo_path, file_path, chunk_size=4000, chunk_index=0) → dict
select_relevant_files(repo_path, issues, llm) → list[str]
read_key_files(repo_path, patterns) → dict[str, str]    # Fallback
```

### LLM Functions:
```python
analyze_repo_with_llm(repo, structure, files, issues, llm) → str
create_plan(llm, repo, analysis, issues, issue_num, user_context) → str
```

### Git Functions (delegate to git_ops):
```python
clone_repo(repo_name) → Path
fork_and_prepare_repo(repo, branch, llm, plan) → str
write_changes(repo_path, changes_text) → str
commit_and_push(repo_path, branch, msg)
create_pr(repo, branch, title, body) → str
```

---

## `cli.py` — CLI Interface

### Functions:
```python
banner()                    # Show ASCII banner
check_config() → bool       # Verify env vars
choose_model() → str        # Model selection menu
collect_repos() → list[str] # Repo input
pick_issue(repo) → int      # Issue selection with table display
show_plan(plan)             # Display plan in Rich Panel
plan_menu() → str           # Approve/Edit/Cancel menu
run()                       # Main loop — orchestrates everything
main()                      # Entry point
```

### The `run()` function flow:
```
1. banner()
2. check_config()
3. choose_model()
4. collect_repos()
5. For each repo:
   a. pick_issue(repo)
   b. Build + run analysis graph (stream events)
   c. show_plan(plan)
   d. plan_menu() → approve/edit/cancel
   e. If approved: build + run execution graph
   f. Display PR URL
```

---

## How `select_relevant_files` Works (OpenCode Strategy)

```
Input: repo_path, issues, llm
    │
    ▼
┌─────────────────────────────────────┐
│ 1. Read file tree (300 files, d=4)  │
│ 2. Keyword search issue titles      │
│    (3 words per issue, 5 issues)    │
│ 3. Deduplicate search hits          │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│ Send to LLM:                        │
│   - File tree                       │
│   - Issues                          │
│   - Search hit files                │
│                                     │
│ "Select 5-10 most relevant files"   │
└───────────────┬─────────────────────┘
                │
                ▼
┌─────────────────────────────────────┐
│ Parse LLM response:                 │
│   - Extract file paths              │
│   - Validate they exist on disk     │
│   - Fallback: search results        │
│   - Fallback: pattern matching      │
└───────────────┬─────────────────────┘
                │
                ▼
Output: list[str] of file paths
```
