"""Provider-agnostic LLM service. No provider configured → unavailable."""

from __future__ import annotations

import json
import logging
import os
import re
from pathlib import Path
from typing import Any, Optional
from uuid import uuid4

import httpx
from dotenv import load_dotenv
from pydantic import ValidationError

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
        fallback_flag = (os.getenv("LLM_FALLBACK_ENABLED") or "false").strip().lower()
        self.fallback_enabled = fallback_flag in {"1", "true", "yes", "on"}

    def is_configured(self) -> bool:
        """True only when a provider and API key are present. Empty META_API_KEY → unavailable."""
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
        attachments: Optional[list[dict]] = None,
    ) -> TeamDefinition:
        if not self.is_configured():
            raise LLMUnavailableError("No LLM provider configured")

        if self.provider in {"meta", "llama", "muse"}:
            return await self._plan_team_openai_compatible(
                goal=goal,
                max_team_size=max_team_size,
                budget_preference=budget_preference,
                available_tools=available_tools,
                attachments=attachments or [],
            )

        raise LLMUnavailableError(f"Unsupported LLM provider: {self.provider}")

    async def _plan_team_openai_compatible(
        self,
        goal: str,
        max_team_size: int,
        budget_preference: str,
        available_tools: list[str],
        attachments: list[dict],
    ) -> TeamDefinition:
        model = self._model_for_planning()
        system = (
            "You are QuestMaster's team planner. Return ONLY a single JSON object "
            "(no markdown, no commentary) for a TeamDefinition with keys: "
            "id, team_name, summary, agents. "
            "Each agent MUST include: id, name, role, purpose, skills (array), "
            "allowed_tools (array), permissions object with data_scopes and actions "
            "arrays, budget object with max_llm_cost_usd (number) and max_llm_calls "
            "(integer). Optional: instructions, guardrails, model_policy, "
            "estimated_cost_usd. "
            f"Return at most {max_team_size} agents. "
            "Prefer tools from available_tools when relevant. "
            "If attachments are provided, tailor the team to use that context."
        )
        user_payload: dict[str, Any] = {
            "goal": goal,
            "max_team_size": max_team_size,
            "budget_preference": budget_preference,
            "available_tools": available_tools,
        }
        if attachments:
            user_payload["attachments"] = attachments

        headers = {
            "Authorization": f"Bearer {self.meta_api_key}",
            "Content-Type": "application/json",
        }

        last_error: Exception | None = None
        # Two attempts: first normal, second with stricter reminder if parse/validate fails.
        for attempt in range(2):
            messages = [
                {"role": "system", "content": system},
                {"role": "user", "content": json.dumps(user_payload)},
            ]
            if attempt == 1:
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your previous reply was invalid. Reply again with ONLY "
                            "valid minified JSON for TeamDefinition. No markdown fences."
                        ),
                    }
                )
            payload = {
                "model": model,
                "messages": messages,
                "temperature": 0.1 if attempt == 0 else 0.0,
            }
            try:
                data = await self._post_chat_completion(headers=headers, payload=payload)
                content = data["choices"][0]["message"]["content"]
                if isinstance(content, list):
                    # Some providers return content parts
                    content = "".join(
                        part.get("text", "") if isinstance(part, dict) else str(part)
                        for part in content
                    )
                parsed = _extract_json_object(str(content))
                coerced = _coerce_team_dict(parsed, max_team_size=max_team_size)
                return TeamDefinition.model_validate(coerced)
            except (
                LLMUnavailableError,
                ValidationError,
                ValueError,
                json.JSONDecodeError,
                KeyError,
                TypeError,
            ) as exc:
                last_error = exc
                logger.warning("LLM plan_team attempt %s failed: %s", attempt + 1, exc)
                continue
            except Exception as exc:
                last_error = exc
                logger.warning("LLM plan_team attempt %s failed: %s", attempt + 1, exc)
                continue

        raise LLMUnavailableError(str(last_error) if last_error else "LLM plan_team failed")

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


def _coerce_team_dict(raw: dict, *, max_team_size: int) -> dict:
    """Fill common missing fields so slightly imperfect LLM JSON still validates."""
    data = dict(raw)
    data.setdefault("id", f"generated-team-{uuid4().hex[:8]}")
    data.setdefault("team_name", "Generated Team")
    data.setdefault("summary", data.get("team_name") or "Generated team")
    agents = data.get("agents") or []
    if not isinstance(agents, list):
        agents = []
    fixed_agents = []
    for i, agent in enumerate(agents[: max(1, max_team_size)]):
        if not isinstance(agent, dict):
            continue
        a = dict(agent)
        a.setdefault("id", f"agent-{i + 1}")
        a.setdefault("name", f"Agent {i + 1}")
        a.setdefault("role", "Specialist")
        a.setdefault("purpose", a.get("role") or "Support the goal.")
        if not isinstance(a.get("skills"), list):
            a["skills"] = [str(a["skills"])] if a.get("skills") else ["general"]
        if not isinstance(a.get("allowed_tools"), list):
            a["allowed_tools"] = []
        perms = a.get("permissions")
        if not isinstance(perms, dict):
            perms = {}
        if not isinstance(perms.get("data_scopes"), list):
            perms["data_scopes"] = []
        if not isinstance(perms.get("actions"), list):
            perms["actions"] = ["read"]
        a["permissions"] = perms
        budget = a.get("budget")
        if not isinstance(budget, dict):
            budget = {}
        try:
            budget["max_llm_cost_usd"] = float(budget.get("max_llm_cost_usd", 0.05))
        except (TypeError, ValueError):
            budget["max_llm_cost_usd"] = 0.05
        try:
            budget["max_llm_calls"] = int(budget.get("max_llm_calls", 2))
        except (TypeError, ValueError):
            budget["max_llm_calls"] = 2
        a["budget"] = budget
        a.setdefault("instructions", "")
        if not isinstance(a.get("guardrails"), list):
            a["guardrails"] = []
        a.setdefault("model_policy", "auto")
        try:
            a["estimated_cost_usd"] = float(a.get("estimated_cost_usd") or 0.0)
        except (TypeError, ValueError):
            a["estimated_cost_usd"] = 0.0
        fixed_agents.append(a)
    if not fixed_agents:
        raise ValueError("LLM team contained no valid agents")
    data["agents"] = fixed_agents
    return data


def _extract_json_object(content: str) -> dict:
    """Parse JSON from model output, tolerating markdown fences and leading prose."""
    text = content.strip()
    if text.startswith("```"):
        lines = text.splitlines()
        if lines and lines[0].startswith("```"):
            lines = lines[1:]
        if lines and lines[-1].strip() == "```":
            lines = lines[:-1]
        text = "\n".join(lines).strip()
    try:
        parsed = json.loads(text)
        if isinstance(parsed, dict):
            return parsed
    except json.JSONDecodeError:
        pass
    start = text.find("{")
    end = text.rfind("}")
    if start >= 0 and end > start:
        return json.loads(text[start : end + 1])
    # Last resort: first {...} block via regex
    match = re.search(r"\{[\s\S]*\}", text)
    if match:
        return json.loads(match.group(0))
    raise json.JSONDecodeError("No JSON object found", text, 0)


_llm_service: Optional[LLMService] = None


def get_llm_service() -> LLMService:
    global _llm_service
    if _llm_service is None:
        _llm_service = LLMService()
    return _llm_service
