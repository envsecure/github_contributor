import os
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()


class Settings:
    GITHUB_TOKEN: str = os.getenv("GITHUB_TOKEN", "")
    GEMINI_API_KEY: str = os.getenv("GEMINI_API_KEY", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemma-3-27b-it")
    WORK_DIR: Path = Path(os.getenv("WORK_DIR", "./workspace"))
    MAX_STEPS: int = int(os.getenv("MAX_STEPS", "30"))


settings = Settings()
settings.WORK_DIR.mkdir(parents=True, exist_ok=True)
