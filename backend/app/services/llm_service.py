"""Provider-agnostic LLM service. No provider configured → unavailable."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Optional

import httpx
from dotenv import load_dotenv

from app.models import TeamDefinition

_REPO_ROOT = Path(__file__).resolve().parents[3]
load_dotenv(_REPO_ROOT / ".env")
load_dotenv()

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """Raised when no provider is configured or the provider call fails."""


class LLMService:
    """Abstraction over LLM providers. Never hardcode model names in callers."""

    def __init__(self) -> None:
        self.provider = (os.getenv("LLM_PROVIDER") or "").strip().lower()
        self.fast_model = (os.getenv("LLM_FAST_MODEL") or "").strip()
        self.strong_model = (os.getenv("LLM_STRONG_MODEL") or "").strip()
        self.meta_api_key = (os.getenv("META_API_KEY") or "").strip()
        self.meta_base_url = (
            os.getenv("META_BASE_URL") or "https://api.meta.ai/v1"
        ).rstrip("/")
        fallback_flag = (os.getenv("LLM_FALLBACK_ENABLED") or "true").strip().lower()
        self.fallback_enabled = fallback_flag in {"1", "true", "yes", "on"}

    def is_configured(self) -> bool:
        """True only when a provider and API key are present. Empty META_API_KEY → fallback."""
        if not self.provider:
            return False
        if self.provider in {"meta", "llama", "muse"}:
            return bool(self.meta_api_key and (self.fast_model or self.strong_model))
        return False

    def _model_for_planning(self) -> str:
        return self.strong_model or self.fast_model

    async def plan_team(
        self,
        goal: str,
        max_team_size: int,
        budget_preference: str,
        available_tools: list[str],
    ) -> TeamDefinition:
        if not self.is_configured():
            raise LLMUnavailableError("No LLM provider configured")

        if self.provider in {"meta", "llama", "muse"}:
            return await self._plan_team_openai_compatible(
                goal=goal,
                max_team_size=max_team_size,
                budget_preference=budget_preference,
                available_tools=available_tools,
            )

        raise LLMUnavailableError(f"Unsupported LLM provider: {self.provider}")

    async def _plan_team_openai_compatible(
        self,
        goal: str,
        max_team_size: int,
        budget_preference: str,
        available_tools: list[str],
    ) -> TeamDefinition:
        model = self._model_for_planning()
        system = (
            "You are QuestMaster's team planner. Return ONLY valid JSON for a "
            "TeamDefinition with keys: id, team_name, summary, agents. "
            "Each agent needs: id, name, role, purpose, skills, allowed_tools, "
            "permissions (data_scopes, actions), budget (max_llm_cost_usd, "
            "max_llm_calls). Optional: instructions, guardrails, model_policy, "
            "estimated_cost_usd. Respect max_team_size and budget_preference."
        )
        user = json.dumps(
            {
                "goal": goal,
                "max_team_size": max_team_size,
                "budget_preference": budget_preference,
                "available_tools": available_tools,
            }
        )

        headers = {
            "Authorization": f"Bearer {self.meta_api_key}",
            "Content-Type": "application/json",
        }
        payload = {
            "model": model,
            "messages": [
                {"role": "developer", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": 0.2,
        }

        try:
            data = await self._post_chat_completion(headers=headers, payload=payload)
            content = data["choices"][0]["message"]["content"]
            parsed = _extract_json_object(content)
            return TeamDefinition.model_validate(parsed)
        except Exception as exc:
            logger.warning("LLM plan_team failed: %s", exc)
            raise LLMUnavailableError(str(exc)) from exc

    async def _post_chat_completion(self, headers: dict, payload: dict) -> dict:
        """POST /chat/completions with CA bundle, then optional local SSL fallback."""
        verify_targets: list[bool | str] = []
        try:
            import certifi

            verify_targets.append(certifi.where())
        except ImportError:
            pass
        verify_targets.append(True)

        # Local Windows often lacks intermediate CAs; allow insecure only in development.
        app_env = (os.getenv("APP_ENV") or "development").strip().lower()
        if app_env in {"development", "dev", "local"}:
            verify_targets.append(False)

        last_exc: Exception | None = None
        for verify in verify_targets:
            try:
                async with httpx.AsyncClient(timeout=90.0, verify=verify) as client:
                    response = await client.post(
                        f"{self.meta_base_url}/chat/completions",
                        headers=headers,
                        json=payload,
                    )
                    response.raise_for_status()
                    if verify is False:
                        logger.warning(
                            "LLM TLS verification disabled for local development SSL workaround"
                        )
                    return response.json()
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                if "CERTIFICATE" in str(exc).upper() or "SSL" in str(exc).upper():
                    logger.info("LLM TLS attempt failed (verify=%s): %s", verify, exc)
                    continue
                raise
        assert last_exc is not None
        raise last_exc

    async def plan_run(self, *args, **kwargs):
        raise NotImplementedError("plan_run is Phase 2")

    async def reason(self, *args, **kwargs):
        raise NotImplementedError("reason is Phase 2")

    async def synthesize(self, *args, **kwargs):
        raise NotImplementedError("synthesize is Phase 2")


def _extract_json_object(content: str) -> dict:
    """Parse JSON from model output, tolerating markdown fences."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start >= 0 and end > start:
            return json.loads(text[start : end + 1])
        raise


_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
