from __future__ import annotations

from typing import Optional

import google.generativeai as genai
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage

from config import settings

genai.configure(api_key=settings.GEMINI_API_KEY)


class GeminiProvider:
    def __init__(self, model: Optional[str] = None) -> None:
        model_name = model or settings.LLM_MODEL
        self.llm = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=0.2,
        )

    def invoke(self, messages: list[BaseMessage]) -> str:
        content = self.llm.invoke(messages).content
        if isinstance(content, list):
            return "".join(
                block.get("text", "") if isinstance(block, dict) else str(block)
                for block in content
            )
        return str(content)

    def invoke_verbose(self, messages: list[BaseMessage], label: str = "Thinking") -> str:
        """Invoke with console progress indicator."""
        from rich.console import Console
        console = Console()
        with console.status(f"[bold cyan]{label}...[/bold cyan]", spinner="dots"):
            return self.invoke(messages)

    @staticmethod
    def list_models() -> list[str]:
        models = []
        for m in genai.list_models():
            if "generateContent" in m.supported_generation_methods:
                name = m.name.replace("models/", "")
                models.append(name)
        return models
