# OSS Contributor Agent v2 — Workflow

## Complete Step-by-Step Flow

This document traces the entire agent workflow from user input to PR creation.

---

## Phase 0: CLI Setup

```
User launches: python main.py
    │
    ▼
┌─────────────────────────────────────────────┐
│  cli.py → main()                            │
│                                              │
│  1. Show banner                              │
│  2. Check config (GITHUB_TOKEN, API_KEY)     │
│  3. Fetch available models from Google AI    │
│  4. User selects model (e.g. gemma-4-31b-it) │
│  5. User enters repo(s)                      │
│  6. User picks issue (or 0 to skip)          │
└─────────────────────────────────────────────┘
```

---

## Phase 1: Analysis Graph

### Step 1 — Parse Input (`parse_input_node`)

```
Input:  "keystonejs/keystone"
Output: repos = ["keystonejs/keystone"]
        step = "parsed"
```

Parses raw user input into `owner/repo` format. Handles:
- Full URLs: `https://github.com/owner/repo` → `owner/repo`
- Shorthand: `owner/repo` → `owner/repo`
- Comma/newline separated lists

---

### Step 2 — Fetch Info (`fetch_info_node`)

```
Input:  repo = "keystonejs/keystone"
Output: repo_analysis = "{name, description, language, stars, ...}"
        step = "info_fetched"
```

Calls GitHub API to get:
- Repository name, description, language
- Star count, open issues count
- Topics, default branch

---

### Step 3 — Clone & Analyze (`clone_and_analyze_node`)

This is the core analysis step. It follows the **OpenCode-style** file discovery strategy:

```
 5%  Clone repository (git clone with GITHUB_TOKEN)
     │
10%  Read file tree (up to 300 files, depth 4)
     │
15%  Fetch recent issues (last 2 days, max 50)
     │
20%  Filter out pull requests
     │
25%  AI detect high-value issues (top 5)
     │
30%  Display selected issues
     │
35%  Keyword search for issue-related code
     │   (searches issue title words in all files)
     │
45%  AI selects relevant files (5-10 files)
     │   (sends tree + search hits + issues to LLM)
     │
50-80%  Read each selected file in chunks
     │   (4000 chars per chunk, max 3 chunks per file)
     │
85%  Analyze code with AI
     │   (sends structure + file contents + issues to LLM)
     │
100% Analysis complete
```

**Key insight**: The agent does NOT read all files. It:
1. Discovers the file tree
2. Searches for keywords from issue titles
3. Asks AI to pick the 5-10 most relevant files
4. Reads only those files (in chunks if large)

---

### Step 4 — Generate Plan (`generate_plan_node`)

```
Input:  repo, analysis, issues, issue_number
Output: proposed_plan = "Step-by-step implementation plan"
        step = "plan_ready"
```

Sends to LLM:
- Repository name
- Analysis from Step 3
- Target issue details
- User context (conversation history)

LLM produces a structured plan with:
- Which files to modify
- Exact code changes
- Tests needed
- Commit message
- Risks/edge cases

---

## Phase 2: User Review

```
┌─────────────────────────────────────────────┐
│  cli.py → show_plan() + plan_menu()         │
│                                              │
│  Displays plan in a Rich Panel               │
│                                              │
│  User choices:                               │
│    [a] Approve → execute                     │
│    [e] Edit    → regenerate with feedback    │
│    [c] Cancel  → exit                        │
└─────────────────────────────────────────────┘
```

If user edits, the plan is regenerated with their feedback.

---

## Phase 3: Execution Graph

### Step 5 — Plan Approved (`plan_approved_node`)

```
Input:  proposed_plan, issues_found
Output: branch_name = "fix-issue-42"
        pr_title = "Fix: description"
        pr_body = "..."
        step = "approved"
```

Generates:
- Branch name from issue number
- PR title and body from the plan

---

### Step 6 — Execute Changes (`execute_changes_node`)

```
10%  Fork repository (via GitHub API)
     │
20%  Clone repository (with GITHUB_TOKEN)
     │
30%  Add fork as remote, fetch
     │
40%  Create branch (git checkout -b)
     │
50%  Ask LLM to generate code changes
     │   (sends plan + repo path to LLM)
     │
60%  Write changes to files
     │   (parses FILE: markers from LLM output)
     │
70%  Stage all (git add .)
     │
80%  Commit (git commit -m)
     │
90%  Push to fork (git push fork branch)
     │
100% Create PR (via GitHub API)
     │
     ▼
  PR URL returned to user
```

---

## Complete Sequence Diagram

```
User          CLI           LangGraph        GitHub API      Google AI      Git
 │              │              │                │               │             │
 │─launch──────▶│              │                │               │             │
 │              │─check config─│                │               │             │
 │              │─list models──────────────────────────────────▶│             │
 │              │◀─models list─────────────────────────────────│             │
 │─select model─│              │                │               │             │
 │─enter repo──▶│              │                │               │             │
 │              │─fetch issues──────────────────▶│              │             │
 │              │◀─issues list──────────────────│              │             │
 │─pick issue──▶│              │                │               │             │
 │              │              │                │               │             │
 │              │─run analysis graph──────────▶│               │             │
 │              │              │─parse input    │               │             │
 │              │              │─fetch info────────────────────▶│             │
 │              │              │─clone repo─────────────────────────────────▶│
 │              │              │─read tree      │               │             │
 │              │              │─fetch issues──────────────────▶│             │
 │              │              │─detect high-value──────────────────────────▶│
 │              │              │─select files───────────────────────────────▶│
 │              │              │─read chunks    │               │             │
 │              │              │─analyze──────────────────────────────────▶ │
 │              │◀─plan────────│                │               │             │
 │              │              │                │               │             │
 │─show plan───▶│              │                │               │             │
 │◀─plan display│              │                │               │             │
 │─approve─────▶│              │                │               │             │
 │              │              │                │               │             │
 │              │─run execution graph─────────▶│               │             │
 │              │              │─fork repo─────────────────────▶│             │
 │              │              │─clone repo─────────────────────────────────▶│
 │              │              │─add remote────────────────────────────────▶│
 │              │              │─create branch─────────────────────────────▶│
 │              │              │─generate code────────────────────────────▶│
 │              │              │─write files    │               │             │
 │              │              │─stage+commit+push────────────────────────▶│
 │              │              │─create PR─────────────────────▶│             │
 │              │◀─PR URL──────│                │               │             │
 │◀─PR URL─────│              │                │               │             │
```

---

## Error Handling

At any point, if a node fails:
1. `error` field is set in AgentState
2. Router sees `state.error` and routes to `END`
3. CLI displays the error message
4. Agent stops gracefully

Common errors:
- Missing `GITHUB_TOKEN` or `GEMINI_API_KEY`
- Repository not found (404)
- Rate limit exceeded
- Git push fails (no permissions)
- LLM returns unparseable output (fallback strategies kick in)

---

## Fallback Strategies

| Situation | Fallback |
|-----------|----------|
| AI returns no valid file paths | Use keyword search results |
| Keyword search returns nothing | Use pattern-based file selection (*.py, *.js, etc.) |
| AI returns no valid issue numbers | Sort by label priority (good first issue > bug > etc.) |
| LLM `.content` returns list | Coerce to string (join content blocks) |
| Repo already cloned | Skip clone, use existing |
| No recent issues found | Analyze code only, suggest improvements |
