from __future__ import annotations

from typing import Optional


class SessionContext:
    def __init__(self) -> None:
        self.repos: list[str] = []
        self.issues: list[dict] = []
        self.analysis: str = ""
        self.plan: str = ""
        self.user_feedback: str = ""
        self.pr_urls: list[str] = []
        self.messages: list[dict] = []
        self.current_step: str = "init"

    def add_message(self, role: str, content: str) -> None:
        self.messages.append({"role": role, "content": content})

    def conversation_history(self) -> str:
        lines = []
        for msg in self.messages[-20:]:
            prefix = "User" if msg["role"] == "user" else "Assistant"
            lines.append(f"[{prefix}]: {msg['content'][:500]}")
        return "\n".join(lines)

    def reset(self) -> None:
        self.__init__()
