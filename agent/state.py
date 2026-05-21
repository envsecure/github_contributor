from __future__ import annotations

from typing import Optional
from dataclasses import dataclass, field


@dataclass
class AgentState:
    # user input
    raw_input: str = ""
    repos: list[str] = field(default_factory=list)

    # selection
    selected_model: str = "gemini-2.0-flash"
    selected_repo: str = ""
    selected_issue: int = 0

    # analysis
    repo_analysis: str = ""
    issues_found: list[dict] = field(default_factory=list)
    issue_details: str = ""

    # planning
    proposed_plan: str = ""
    plan_approved: bool = False
    user_feedback: str = ""

    # execution
    branch_name: str = ""
    pr_title: str = ""
    pr_body: str = ""
    pr_url: str = ""
    changes_made: str = ""
    error: Optional[str] = None

    # routing
    step: str = "awaiting_input"
    done: bool = False
