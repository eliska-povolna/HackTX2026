"""Cheap-safe-adequate routing metrics and budget gates."""

from __future__ import annotations

import os
from dataclasses import dataclass, field


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
    # Assumed cost if every task were an LLM call
    baseline_per_task_usd: float = 0.007

    def record_code(self) -> None:
        self.code_tasks += 1
        self.llm_calls_avoided += 1
        self.estimated_baseline_cost_usd += self.baseline_per_task_usd

    def record_tool(self) -> None:
        self.tool_tasks += 1
        self.llm_calls_avoided += 1
        self.estimated_baseline_cost_usd += self.baseline_per_task_usd

    def can_afford_llm(self, cost_usd: float = 0.004) -> bool:
        if self.llm_calls + 1 > self.max_llm_calls:
            return False
        if self.estimated_ai_cost_usd + cost_usd > self.max_estimated_cost_usd:
            return False
        return True

    def record_llm(self, cost_usd: float = 0.004, in_tok: int = 800, out_tok: int = 250) -> bool:
        """Return False if budget would be exceeded."""
        if not self.can_afford_llm(cost_usd):
            return False
        self.llm_calls += 1
        self.llm_tasks += 1
        self.estimated_ai_cost_usd = round(self.estimated_ai_cost_usd + cost_usd, 6)
        self.input_tokens += in_tok
        self.output_tokens += out_tok
        self.estimated_baseline_cost_usd += self.baseline_per_task_usd
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
        }
