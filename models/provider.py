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
        return self.llm.invoke(messages).content

    @staticmethod
    def list_models() -> list[str]:
        models = []
        for m in genai.list_models():
            if "generateContent" in m.supported_generation_methods:
                name = m.name.replace("models/", "")
                models.append(name)
        return models
