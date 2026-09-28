"""Settings loaded from environment (.env supported). Secrets are never logged."""
from __future__ import annotations

import os
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parent.parent
load_dotenv(ROOT / ".env")


def _abs(p: str) -> str:
    path = Path(p)
    return str(path if path.is_absolute() else ROOT / path)


@dataclass(frozen=True)
class Settings:
    groq_api_key: str
    groq_model: str
    groq_fallback_model: str
    memory_backend: str
    hindsight_base_url: str
    hindsight_api_key: str
    hindsight_bank_id: str
    hindsight_timeout: float
    db_path: str
    local_memory_path: str
    price_tolerance_pct: Decimal

    def redacted(self) -> dict:
        """Safe-to-display view: presence of secrets only, never values."""
        return {
            "groq_key_set": bool(self.groq_api_key),
            "groq_model": self.groq_model,
            "groq_fallback_model": self.groq_fallback_model,
            "memory_backend": self.memory_backend,
            "hindsight_base_url": self.hindsight_base_url or None,
            "hindsight_key_set": bool(self.hindsight_api_key),
            "hindsight_bank_id": self.hindsight_bank_id,
            "price_tolerance_pct": str(self.price_tolerance_pct),
        }


def load_settings() -> Settings:
    return Settings(
        groq_api_key=os.getenv("GROQ_API_KEY", "").strip(),
        groq_model=os.getenv("GROQ_MODEL", "openai/gpt-oss-120b"),
        groq_fallback_model=os.getenv("GROQ_FALLBACK_MODEL", "qwen/qwen3-32b"),
        memory_backend=os.getenv("MEMORY_BACKEND", "auto").strip().lower(),
        hindsight_base_url=os.getenv("HINDSIGHT_BASE_URL", "").strip(),
        hindsight_api_key=os.getenv("HINDSIGHT_API_KEY", "").strip(),
        hindsight_bank_id=os.getenv("HINDSIGHT_BANK_ID", "memoryops-ap"),
        hindsight_timeout=float(os.getenv("HINDSIGHT_TIMEOUT", "60")),
        db_path=_abs(os.getenv("DB_PATH", "backend/var/memoryops.db")),
        local_memory_path=_abs(os.getenv("LOCAL_MEMORY_PATH", "backend/var/local_memory.json")),
        price_tolerance_pct=Decimal(os.getenv("PRICE_TOLERANCE_PCT", "0.5")),
    )


settings = load_settings()
