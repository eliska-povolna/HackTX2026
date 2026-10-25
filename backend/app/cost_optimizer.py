"""Cheap-safe-adequate routing metrics and budget gates.

llm_calls_avoided baseline: each CODE/TOOL task (and a skipped optional LLM reviewer)
counts as one avoided LLM call versus a hypothetical full-LLM execution of every task.
This is an internal comparison baseline, not independently measured market savings.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from app.models import AgentDefinition


@dataclass
class RunCostTracker:
    max_llm_calls: int = field(
        default_factory=lambda: int(os.getenv("MAX_LLM_CALLS_PER_RUN") or 6)
    )
    max_estimated_cost_usd: float = field(
        default_factory=lambda: float(os.getenv("MAX_ESTIMATED_RUN_COST_USD") or 0.25)
    )
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
    # Assumed cost if every task were an LLM call
    baseline_per_task_usd: float = 0.007

    def record_route(self) -> None:
        self.routing_decisions += 1

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
        if self.llm_calls + 1 > self.max_llm_calls:
            return False
        if self.estimated_ai_cost_usd + cost_usd > self.max_estimated_cost_usd:
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
    ) -> bool:
        """Return False if budget would be exceeded. Caller should pass agent for gate."""
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
        return True

    def should_run_reviewer(self, quality_mode: str, high_risk: bool) -> bool:
        return quality_mode == "strict" or high_risk

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
        }
