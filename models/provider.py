from __future__ import annotations

from typing import Optional

from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import BaseMessage

from config import settings


class GeminiProvider:
    MODELS = [
        "gemini-2.0-flash",
        "gemini-2.0-flash-lite",
        "gemini-1.5-flash",
        "gemini-1.5-pro",
        "gemma-3-27b-it",
        "gemma-3-12b-it",
        "gemma-2-27b-it",
        "gemma-2-9b-it",
        "gemma-2-2b-it",
    ]

    def __init__(self, model: Optional[str] = None) -> None:
        model_name = model or settings.LLM_MODEL
        self.llm = ChatGoogleGenerativeAI(
            model=model_name,
            google_api_key=settings.GEMINI_API_KEY,
            temperature=0.2,
        )

    def invoke(self, messages: list[BaseMessage]) -> str:
        return self.llm.invoke(messages).content

    @classmethod
    def list_models(cls) -> list[str]:
        return cls.MODELS
