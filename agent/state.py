from __future__ import annotations

from typing import Optional
from dataclasses import dataclass, field

from config import settings


@dataclass
class AgentState:
    # user input
    raw_input: str = ""
    repos: list[str] = field(default_factory=list)

    # selection
    selected_model: str = settings.LLM_MODEL
    selected_repo: str = ""
    selected_issue: int = 0

    # analysis
    repo_analysis: str = ""
    issues_found: list[dict] = field(default_factory=list)
    issue_details: str = ""
    selected_files: list[str] = field(default_factory=list)
    search_results: str = ""
    file_contents: dict[str, str] = field(default_factory=dict)

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
