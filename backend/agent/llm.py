"""Groq LLM chooser. The model only picks one of the pre-computed options and explains it.

Primary model uses strict JSON-schema output (supported by openai/gpt-oss-* on Groq); the fallback
(qwen/qwen3-32b) uses JSON-object mode plus Pydantic validation. Every response is validated;
failures are retried with exponential backoff, then the next model is tried, and finally the
caller gets None and escalates.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Literal, Optional

from pydantic import BaseModel, Field, ValidationError, field_validator

log = logging.getLogger("memoryops.llm")

Action = Literal["approve", "reject", "adjust", "escalate"]


class LLMChoice(BaseModel):
    action: Action
    cited_precedent_ids: list[str]
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str = Field(min_length=10, max_length=1500)
    precedents_conflict: bool

    @field_validator("cited_precedent_ids")
    @classmethod
    def _dedupe(cls, v: list[str]) -> list[str]:
        return list(dict.fromkeys(v))


SYSTEM_PROMPT = """You are MemoryOps, an accounts-payable assistant. You RECOMMEND ONLY; a human approves every decision and nothing is paid or posted.

You are given one invoice exception (already detected and measured by deterministic code), the allowed resolution options (amounts already computed), and the precedents recalled from the team's memory.

Rules:
- Choose exactly one of the allowed option actions.
- Base the choice ONLY on the recalled precedents provided. Cite the IDs of every precedent you relied on, copied exactly. Never cite an ID that is not in the list. Never invent a precedent, policy, amount or approver.
- If the precedents conflict, if the current case is clearly outside the range the precedents cover, or if they do not really address this case, choose "escalate" and say why.
- Recent precedents outweigh older ones when a vendor's pattern has changed.
- Do not do arithmetic; use the numbers given.
- Keep the rationale to 2-4 plain sentences a finance clerk can check, and mention a required approver if the precedents name one.
Respond with a JSON object only."""


def _schema(allowed_ids: list[str], allowed_actions: list[str]) -> dict:
    return {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": allowed_actions},
            "cited_precedent_ids": {"type": "array", "items": {"type": "string", "enum": allowed_ids}},
            "confidence": {"type": "number"},
            "rationale": {"type": "string"},
            "precedents_conflict": {"type": "boolean"},
        },
        "required": ["action", "cited_precedent_ids", "confidence", "rationale", "precedents_conflict"],
        "additionalProperties": False,
    }


_THINK = re.compile(r"<think>.*?</think>", re.S)


def _parse(content: str) -> dict:
    content = _THINK.sub("", content or "").strip()
    if content.startswith("```"):
        content = content.strip("`")
        content = content[content.find("{"):]
    start, end = content.find("{"), content.rfind("}")
    if start < 0 or end < 0:
        raise ValueError("no JSON object in response")
    return json.loads(content[start:end + 1])


class GroqChooser:
    def __init__(self, api_key: str, model: str, fallback_model: str, client=None,
                 attempts_per_model: int = 3, base_delay: float = 0.6, sleep=time.sleep):
        if client is None:
            from groq import Groq

            client = Groq(api_key=api_key, max_retries=0, timeout=45.0)
        self.client = client
        self.models = [m for m in (model, fallback_model) if m]
        self.attempts = attempts_per_model
        self.base_delay = base_delay
        self.sleep = sleep
        self.last_model: Optional[str] = None
        self.last_errors: list[str] = []

    def choose(self, case: dict, allowed_ids: list[str], allowed_actions: list[str]) -> Optional[LLMChoice]:
        self.last_errors = []
        user = (
            "Case (JSON):\n" + json.dumps(case, indent=1)
            + "\n\nReturn JSON with keys: action, cited_precedent_ids, confidence (0-1), rationale, precedents_conflict."
        )
        messages = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user}]
        for i, model in enumerate(self.models):
            strict = model.startswith("openai/gpt-oss")
            if strict:
                fmt = {"type": "json_schema",
                       "json_schema": {"name": "ap_recommendation", "strict": True,
                                       "schema": _schema(allowed_ids, allowed_actions)}}
            else:
                fmt = {"type": "json_object"}
            for attempt in range(self.attempts):
                try:
                    resp = self.client.chat.completions.create(
                        model=model, messages=messages, response_format=fmt, temperature=0.1,
                        max_completion_tokens=1200,
                    )
                    data = _parse(resp.choices[0].message.content)
                    choice = LLMChoice.model_validate(data)
                    if choice.action not in allowed_actions:
                        raise ValueError(f"action {choice.action!r} not allowed")
                    bad = [c for c in choice.cited_precedent_ids if c not in allowed_ids]
                    if bad:
                        raise ValueError(f"cited unknown ids {bad}")
                    self.last_model = model
                    return choice
                except (ValidationError, ValueError, json.JSONDecodeError) as e:
                    self.last_errors.append(f"{model}#{attempt + 1}: invalid output ({type(e).__name__})")
                except Exception as e:  # network / rate limit / API error; never log request headers
                    self.last_errors.append(f"{model}#{attempt + 1}: {type(e).__name__}")
                if attempt < self.attempts - 1:
                    self.sleep(self.base_delay * (2 ** attempt))
        log.warning("LLM failed on all models: %s", "; ".join(self.last_errors))
        return None
