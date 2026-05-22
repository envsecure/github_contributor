# OSS Contributor Agent v2 — Architecture Overview

## What This Agent Does

This agent automatically:
1. Takes a GitHub repo as input
2. Fetches and analyzes open issues
3. Clones and reads the codebase (intelligently, not blindly)
4. Generates an implementation plan for a chosen issue
5. Forks the repo, writes code, commits, pushes, and creates a PR

All powered by Google Gemini (via LangChain) and GitHub API (via PyGithub).

---

## High-Level Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        USER (CLI)                               │
│  cli.py — Rich terminal UI, user prompts, progress display      │
└──────────────────────────┬──────────────────────────────────────┘
                           │
                           ▼
┌─────────────────────────────────────────────────────────────────┐
│                    LangGraph Pipeline                            │
│                                                                  │
│  ┌──────────┐   ┌───────────┐   ┌──────────────┐   ┌─────────┐ │
│  │  Parse    │──▶│  Fetch    │──▶│  Clone &     │──▶│ Generate│ │
│  │  Input    │   │  Info     │   │  Analyze     │   │  Plan   │ │
│  └──────────┘   └───────────┘   └──────────────┘   └─────────┘ │
│                                                                  │
│  ┌──────────────┐   ┌──────────────┐                            │
│  │  Plan        │──▶│  Execute     │                            │
│  │  Approved    │   │  Changes     │                            │
│  └──────────────┘   └──────────────┘                            │
└──────────────────────────┬──────────────────────────────────────┘
                           │
          ┌────────────────┼────────────────┐
          ▼                ▼                ▼
┌──────────────┐  ┌──────────────┐  ┌──────────────┐
│  GitHub API  │  │  Google AI   │  │  Git Ops     │
│  (PyGithub)  │  │  (Gemini)    │  │  (subprocess)│
│              │  │              │  │              │
│  - Issues    │  │  - Analyze   │  │  - Clone     │
│  - Fork      │  │  - Plan      │  │  - Branch    │
│  - PR        │  │  - Code gen  │  │  - Commit    │
│  - Repo info │  │  - File pick │  │  - Push      │
└──────────────┘  └──────────────┘  └──────────────┘
```

---

## Directory Structure

```
github_contributor/
│
├── main.py                  # Entry point → calls cli.main()
├── cli.py                   # Rich CLI: menus, prompts, progress
├── config.py                # Settings from .env
├── context.py               # SessionContext: mutable session state
│
├── agent/
│   ├── state.py             # AgentState dataclass (LangGraph state)
│   ├── graph.py             # LangGraph StateGraph definitions
│   ├── nodes.py             # Graph node functions (the actual logic)
│   └── tools.py             # Tool functions (GitHub, LLM, file ops)
│
├── models/
│   └── provider.py          # GeminiProvider: wraps ChatGoogleGenerativeAI
│
├── github_client/
│   └── client.py            # GitHubClient: wraps PyGithub
│
├── git_ops.py               # All git/fork/push/PR operations
│
├── workspace/               # Cloned repos live here
└── docs/                    # This documentation
```

---

## Core Components

### 1. `config.py` — Settings

Loads from `.env` file:
```
GITHUB_TOKEN      → GitHub API authentication
GEMINI_API_KEY    → Google AI authentication
LLM_MODEL         → Default model (gemma-3-27b-it)
WORK_DIR          → Where repos are cloned (./workspace)
MAX_STEPS         → LangGraph recursion limit (30)
```

### 2. `context.py` — SessionContext

Mutable state shared across the entire session:
```python
class SessionContext:
    repos: list[str]           # User-provided repos
    issues: list[dict]         # Fetched issues
    analysis: str              # LLM analysis output
    plan: str                  # Generated implementation plan
    user_feedback: str         # User edits to the plan
    pr_urls: list[str]         # Created PR URLs
    messages: list[dict]       # Conversation history
```

### 3. `agent/state.py` — AgentState

LangGraph state that flows through the graph nodes:
```python
@dataclass
class AgentState:
    # Input
    raw_input: str
    repos: list[str]
    selected_model: str
    selected_repo: str
    selected_issue: int

    # Analysis results
    repo_analysis: str
    issues_found: list[dict]
    selected_files: list[str]
    file_contents: dict[str, str]

    # Planning
    proposed_plan: str
    plan_approved: bool

    # Execution
    branch_name: str
    pr_url: str
    changes_made: str

    # Routing
    step: str          # Controls graph routing
    error: str | None
    done: bool
```

### 4. `models/provider.py` — GeminiProvider

Wraps `ChatGoogleGenerativeAI` from LangChain:
- `invoke(messages)` → Always returns `str` (coerces list content)
- `invoke_verbose(messages, label)` → Same but shows spinner
- `list_models()` → Fetches available models from Google AI API

### 5. `github_client/client.py` — GitHubClient

Wraps PyGithub:
- `get_repo(name)` → Repository object
- `get_open_issues(repo, limit, recent_days)` → Filtered issues
- `create_fork(repo)` → Fork the repo
- `create_pull_request(...)` → Create a PR

### 6. `git_ops.py` — Git Operations

All git commands go through here (never raw subprocess in agent code):
- `clone(repo)` → Clone with GITHUB_TOKEN auth
- `fork(repo)` → Fork via API
- `add_remote()`, `fetch()`, `checkout_new_branch()`
- `stage_all()`, `commit()`, `push()`
- `create_pr()` → PR via API
- `fork_branch_commit_push()` → Full workflow

---

## Data Flow

```
User Input (repo name)
    │
    ▼
┌─────────────────┐     ┌──────────────────┐
│  GitHub API     │────▶│  Repo metadata   │
│  get_repo()     │     │  (stars, lang,   │
│                 │     │   topics, branch) │
└─────────────────┘     └──────────────────┘
    │
    ▼
┌─────────────────┐     ┌──────────────────┐
│  GitHub API     │────▶│  Issues list     │
│  get_issues()   │     │  (filtered:      │
│                 │     │   2 days, 50 max)│
└─────────────────┘     └──────────────────┘
    │
    ▼
┌─────────────────┐     ┌──────────────────┐
│  git clone      │────▶│  Local repo at   │
│  (with token)   │     │  workspace/      │
└─────────────────┘     └──────────────────┘
    │
    ▼
┌─────────────────┐     ┌──────────────────┐
│  File discovery │────▶│  Selected files  │
│  (glob + grep   │     │  (5-10 files)    │
│   + AI select)  │     │                  │
└─────────────────┘     └──────────────────┘
    │
    ▼
┌─────────────────┐     ┌──────────────────┐
│  Gemini LLM     │────▶│  Analysis +      │
│  (analyze)      │     │  Plan + Code     │
└─────────────────┘     └──────────────────┘
    │
    ▼
┌─────────────────┐     ┌──────────────────┐
│  git_ops        │────▶│  PR created      │
│  (fork+push+PR) │     │  on GitHub       │
└─────────────────┘     └──────────────────┘
```

---

## External Dependencies

| Package | Purpose |
|---------|---------|
| `langgraph` | Graph-based agent orchestration |
| `langchain-core` | Message types (SystemMessage, HumanMessage) |
| `langchain-google-genai` | Gemini LLM wrapper |
| `google-generativeai` | Google AI API (model listing) |
| `PyGithub` | GitHub REST API |
| `rich` | Terminal UI (tables, panels, spinners) |
| `python-dotenv` | Load .env files |

---

## Two-Phase Pipeline

The agent runs two separate LangGraph pipelines:

### Phase 1: Analysis Graph
```
parse_input → fetch_info → clone_and_analyze → generate_plan → END
```
Produces: implementation plan for a chosen issue.

### Phase 2: Execution Graph
```
plan_approved → execute_changes → END
```
Produces: PR URL with the implemented changes.

The user reviews the plan between phases and can approve, edit, or cancel.
