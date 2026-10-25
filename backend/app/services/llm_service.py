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


def _toyota_capability_contract() -> str:
    try:
        from app.workflow_validate import capability_catalog_for_prompt

        return " " + capability_catalog_for_prompt()
    except Exception:
        return ""


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

    def resolve_model(self, tier: str) -> str:
        """Return configured model name for FAST or STRONG. Raises if missing."""
        if tier.upper() == "FAST":
            if not self.fast_model:
                raise LLMUnavailableError("LLM_FAST_MODEL is not configured")
            return self.fast_model
        if tier.upper() == "STRONG":
            if not self.strong_model:
                raise LLMUnavailableError("LLM_STRONG_MODEL is not configured")
            return self.strong_model
        raise LLMUnavailableError(f"Unknown model tier: {tier}")

    def _model_for_planning(self) -> str:
        """Legacy helper — prefer ModelRouter.choose_model for new call sites."""
        return self.strong_model or self.fast_model

    async def plan_team(
        self,
        goal: str,
        max_team_size: int,
        budget_preference: str,
        available_tools: list[str],
        attachments: Optional[list[dict]] = None,
        work_style: str = "balanced",
        context_source_ids: Optional[list[str]] = None,
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
                work_style=work_style,
                context_source_ids=context_source_ids or [],
            )

        raise LLMUnavailableError(f"Unsupported LLM provider: {self.provider}")

    async def _plan_team_openai_compatible(
        self,
        goal: str,
        max_team_size: int,
        budget_preference: str,
        available_tools: list[str],
        attachments: list[dict],
        work_style: str = "balanced",
        context_source_ids: Optional[list[str]] = None,
    ) -> TeamDefinition:
        from app.model_router import ModelRoutingError, choose_model

        try:
            decision = choose_model(task_kind="planning", work_style=work_style)
        except ModelRoutingError as exc:
            raise LLMUnavailableError(str(exc)) from exc
        model = decision.model
        system = (
            "You are QuestMaster's team planner. Return ONLY a single JSON object "
            "(no markdown, no commentary) for a TeamDefinition with keys: "
            "id, team_name, summary, agents, optional policy "
            "{max_llm_calls, max_llm_cost_usd, work_style, guardrails, workflow_kind, scope}. "
            "Each agent MUST include: id, name, role, purpose, skills (array), "
            "allowed_tools (array), permissions object with data_scopes and actions "
            "arrays, budget object with max_llm_cost_usd (number > 0) and max_llm_calls "
            "(integer > 0). Optional: instructions, guardrails, model_policy, "
            "estimated_cost_usd, allowed_integrations (named refs like nessie/meta — "
            "never include secret values). "
            "Give agents distinct human-readable names AND distinct ids/roles so tasks "
            "can be assigned without collapsing onto one agent. "
            f"Return at most {max_team_size} agents. "
            "Prefer tools from available_tools when relevant. "
            "Match the team to the goal domain — hospitality, research, support, "
            "engineering, etc. Only plan a Toyota/mobility financial team when the "
            "goal or enabled context_sources clearly involve vehicle/mobility/"
            "Toyota customer decisions. Do not force Toyota roles for unrelated goals. "
            "If attachments or context_sources are provided, tailor the team to use them. "
            "Prefer CODE/TOOL capabilities for deterministic work; only create AGENT "
            "(LLM) responsibilities when judgment or synthesis is required. "
            "For a portfolio mobility or vehicle-loan targeting goal, prefer TWO responsibilities: "
            "Financial Context Analyst (load_toyota_portfolio, build_portfolio_financials, nessie_load_customer_financials; raw/summary scopes; read/calculate; nessie grant) and "
            "Shortlist Strategist (rank_customers, score_mobility_options for mobility, explain_shortlist; financial_summary; read/calculate/synthesize/recommend; meta grant). "
            "Use scope=portfolio and workflow_kind=loan_opportunity or mobility_decision. Loan targeting ranks financial fit for ALL customers; do NOT describe mobility relevance filtering or option scoring in its roles/skills. Only mobility adds filter_mobility_portfolio and evaluate_mobility_relevance to finance, and score_mobility_options to the strategist. "
            "For SINGLE-CUSTOMER mobility only: " + _toyota_capability_contract()
        )
        user_payload: dict[str, Any] = {
            "goal": goal,
            "max_team_size": max_team_size,
            "budget_preference": budget_preference,
            "available_tools": available_tools,
        }
        if attachments:
            user_payload["attachments"] = attachments
        if context_source_ids:
            user_payload["context_source_ids"] = list(context_source_ids)

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
        if not isinstance(a.get("allowed_integrations"), list):
            a["allowed_integrations"] = []
        fixed_agents.append(a)
    if not fixed_agents:
        raise ValueError("LLM team contained no valid agents")
    data["agents"] = fixed_agents
    policy = data.get("policy")
    if policy is not None and not isinstance(policy, dict):
        data.pop("policy", None)
    elif isinstance(policy, dict):
        policy.setdefault("work_style", "balanced")
        policy.setdefault("max_llm_calls", 6)
        policy.setdefault("max_llm_cost_usd", 0.20)
        if not isinstance(policy.get("guardrails"), list):
            policy["guardrails"] = []
        data["policy"] = policy
    return data


def extract_chat_message_text(raw: object) -> str:
    """Pull assistant text from OpenAI-compatible (and slight variants) chat payloads."""

    def _from_content(content: object) -> str:
        if content is None:
            return ""
        if isinstance(content, str):
            return content.strip()
        if isinstance(content, list):
            parts: list[str] = []
            for part in content:
                if isinstance(part, str):
                    parts.append(part)
                elif isinstance(part, dict):
                    for key in ("text", "content", "value"):
                        value = part.get(key)
                        if isinstance(value, str) and value.strip():
                            parts.append(value)
                            break
            return "".join(parts).strip()
        return str(content).strip()

    if not isinstance(raw, dict):
        return ""

    choices = raw.get("choices")
    if isinstance(choices, list) and choices:
        choice = choices[0] if isinstance(choices[0], dict) else {}
        message = choice.get("message") if isinstance(choice, dict) else None
        if isinstance(message, dict):
            for key in ("content", "text", "reasoning", "output_text"):
                text = _from_content(message.get(key))
                if text:
                    return text
        for key in ("text", "content"):
            text = _from_content(choice.get(key) if isinstance(choice, dict) else None)
            if text:
                return text

    for key in ("output_text", "text", "content"):
        text = _from_content(raw.get(key))
        if text:
            return text

    output = raw.get("output")
    if isinstance(output, list):
        chunks: list[str] = []
        for item in output:
            if not isinstance(item, dict):
                continue
            chunks.append(_from_content(item.get("content") or item.get("text")))
        joined = "\n".join(chunk for chunk in chunks if chunk).strip()
        if joined:
            return joined

    return ""


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
