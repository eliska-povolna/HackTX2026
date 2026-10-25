"""Cheap-safe-adequate routing metrics and budget gates.

Budget layers (all must pass for an LLM call):
1. Server safety ceiling (MAX_LLM_CALLS_PER_RUN / MAX_ESTIMATED_RUN_COST_USD)
2. Team policy budget (TeamPolicy.max_llm_*)
3. Assigned agent budget (agent.budget.max_llm_*)

llm_calls_avoided baseline: each CODE/TOOL task (and a skipped optional LLM reviewer)
counts as one avoided LLM call versus a hypothetical full-LLM execution of every task.
This is an internal comparison baseline, not independently measured market savings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models import AgentDefinition, TeamPolicy


@dataclass
class RunCostTracker:
    max_llm_calls: int = field(
        default_factory=lambda: int(os.getenv("MAX_LLM_CALLS_PER_RUN") or 6)
    )
    max_estimated_cost_usd: float = field(
        default_factory=lambda: float(os.getenv("MAX_ESTIMATED_RUN_COST_USD") or 0.25)
    )
    team_max_llm_calls: Optional[int] = None
    team_max_llm_cost_usd: Optional[float] = None
    work_style: str = "balanced"
    llm_calls: int = 0
    llm_calls_avoided: int = 0
    input_tokens: int = 0
    output_tokens: int = 0
    estimated_ai_cost_usd: float = 0.0
    estimated_baseline_cost_usd: float = 0.0
    code_tasks: int = 0
    tool_tasks: int = 0
    llm_tasks: int = 0
    routing_decisions: int = 0
    agent_llm_calls: dict[str, int] = field(default_factory=dict)
    agent_llm_cost: dict[str, float] = field(default_factory=dict)
    last_model_tier: Optional[str] = None
    model_tiers_used: list[str] = field(default_factory=list)
    # Assumed cost if every task were an LLM call
    baseline_per_task_usd: float = 0.007

    @classmethod
    def from_team_policy(cls, policy: Optional["TeamPolicy"]) -> "RunCostTracker":
        tracker = cls()
        if policy is not None:
            tracker.team_max_llm_calls = policy.max_llm_calls
            tracker.team_max_llm_cost_usd = policy.max_llm_cost_usd
            tracker.work_style = (policy.work_style or "balanced").strip().lower()
            # Effective run ceiling is the tighter of server vs team
            tracker.max_llm_calls = min(tracker.max_llm_calls, policy.max_llm_calls)
            tracker.max_estimated_cost_usd = min(
                tracker.max_estimated_cost_usd, policy.max_llm_cost_usd
            )
        return tracker

    def record_route(self) -> None:
        self.routing_decisions += 1

    def record_model_tier(self, tier: str) -> None:
        self.last_model_tier = tier
        self.model_tiers_used.append(tier)

    def record_code(self) -> None:
        self.code_tasks += 1
        self.llm_calls_avoided += 1
        self.estimated_baseline_cost_usd += self.baseline_per_task_usd

    def record_tool(self) -> None:
        self.tool_tasks += 1
        self.llm_calls_avoided += 1
        self.estimated_baseline_cost_usd += self.baseline_per_task_usd

    def record_avoided_optional_llm(self) -> None:
        """Reviewer (or similar) would have been an LLM task but was skipped by policy."""
        self.llm_calls_avoided += 1
        self.estimated_baseline_cost_usd += self.baseline_per_task_usd

    def can_afford_llm(
        self,
        cost_usd: float = 0.004,
        agent: Optional["AgentDefinition"] = None,
    ) -> bool:
        # Server (+ team-tightened) ceiling
        if self.llm_calls + 1 > self.max_llm_calls:
            return False
        if self.estimated_ai_cost_usd + cost_usd > self.max_estimated_cost_usd:
            return False
        # Explicit team ceiling (same as tightened max when from_team_policy used)
        if self.team_max_llm_calls is not None and self.llm_calls + 1 > self.team_max_llm_calls:
            return False
        if (
            self.team_max_llm_cost_usd is not None
            and self.estimated_ai_cost_usd + cost_usd > self.team_max_llm_cost_usd
        ):
            return False
        if agent is not None:
            aid = agent.id
            if self.agent_llm_calls.get(aid, 0) + 1 > agent.budget.max_llm_calls:
                return False
            if self.agent_llm_cost.get(aid, 0.0) + cost_usd > agent.budget.max_llm_cost_usd:
                return False
        return True

    def record_llm(
        self,
        cost_usd: float = 0.004,
        in_tok: int = 800,
        out_tok: int = 250,
        agent_id: Optional[str] = None,
        model_tier: Optional[str] = None,
    ) -> bool:
        """Caller must check can_afford_llm first."""
        self.llm_calls += 1
        self.llm_tasks += 1
        self.estimated_ai_cost_usd = round(self.estimated_ai_cost_usd + cost_usd, 6)
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.estimated_baseline_cost_usd += self.baseline_per_task_usd
        if agent_id:
            self.agent_llm_calls[agent_id] = self.agent_llm_calls.get(agent_id, 0) + 1
            self.agent_llm_cost[agent_id] = round(
                self.agent_llm_cost.get(agent_id, 0.0) + cost_usd, 6
            )
        if model_tier:
            self.record_model_tier(model_tier)
        return True

    def should_run_reviewer(
        self,
        *,
        work_style: str,
        quality_mode: str,
        high_risk: bool,
        has_reviewer: bool,
    ) -> bool:
        if not has_reviewer:
            return False
        if high_risk or quality_mode == "strict":
            return True
        if (work_style or "").lower() == "thorough":
            return True
        return False

    def metrics_dict(self) -> dict:
        saved = max(0.0, self.estimated_baseline_cost_usd - self.estimated_ai_cost_usd)
        return {
            "llm_calls": self.llm_calls,
            "llm_calls_avoided": self.llm_calls_avoided,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "estimated_ai_cost_usd": round(self.estimated_ai_cost_usd, 6),
            "estimated_baseline_cost_usd": round(self.estimated_baseline_cost_usd, 6),
            "estimated_cost_saved_usd": round(saved, 6),
            "code_tasks": self.code_tasks,
            "tool_tasks": self.tool_tasks,
            "llm_tasks": self.llm_tasks,
            "routing_decisions": self.routing_decisions,
            "agent_llm_calls": dict(self.agent_llm_calls),
            "agent_llm_cost": dict(self.agent_llm_cost),
            "work_style": self.work_style,
            "last_model_tier": self.last_model_tier,
            "model_tiers_used": list(self.model_tiers_used),
        }
