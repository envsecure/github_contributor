from __future__ import annotations

from datetime import datetime, timedelta, timezone

from github import Github
from github.Issue import Issue
from github.Repository import Repository
from github.PullRequest import PullRequest

from config import settings


class GitHubClient:
    def __init__(self) -> None:
        self._client = Github(settings.GITHUB_TOKEN)

    @property
    def client(self) -> Github:
        return self._client

    def get_user(self) -> str:
        return self._client.get_user().login

    def get_repo(self, full_name: str) -> Repository:
        return self._client.get_repo(full_name)

    def search_issues(self, query: str, limit: int = 10) -> list[Issue]:
        return list(self._client.search_issues(query, sort="updated", order="desc"))[:limit]

    def get_open_issues(
        self,
        repo_full_name: str,
        state: str = "open",
        limit: int = 50,
        recent_days: int = 2,
    ) -> list[Issue]:
        repo = self.get_repo(repo_full_name)
        since = datetime.now(timezone.utc) - timedelta(days=recent_days)
        issues = repo.get_issues(state=state, sort="created", direction="desc", since=since)
        results = []
        for issue in issues:
            if issue.pull_request:
                continue
            results.append(issue)
            if len(results) >= limit:
                break
        return results

    def create_fork(self, repo_full_name: str) -> Repository:
        return self.get_repo(repo_full_name).create_fork()

    def create_pull_request(
        self,
        repo_full_name: str,
        head: str,
        base: str,
        title: str,
        body: str,
    ) -> PullRequest:
        return self.get_repo(repo_full_name).create_pull(
            title=title, body=body, head=head, base=base
        )
