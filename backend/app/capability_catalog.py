"""Installed capability catalog — what this QuestMaster environment actually supports.

HackTX ships one complete pack: Toyota Mobility / Financial Decision.
The platform is reusable only where listed connections + functions exist.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional


class CapabilityKind(str, Enum):
    CONNECTION = "connection"
    FUNCTION = "function"
    AI = "ai"
    DATA = "data"


@dataclass(frozen=True)
class InstalledCapability:
    id: str
    kind: CapabilityKind
    label: str
    description: str
    pack: str = "toyota_mobility"
    action: Optional[str] = None  # runtime action name when applicable
    installed: bool = True


# --- Toyota Mobility / Financial Decision Pack (installed) ---

TOYOTA_PACK_ID = "toyota_mobility"

INSTALLED: tuple[InstalledCapability, ...] = (
    # Connections / data
    InstalledCapability(
        id="toyota_customer_profiles",
        kind=CapabilityKind.CONNECTION,
        label="Toyota customer profiles",
        description="Synthetic Toyota customer identity and scenario labels.",
        action="load_toyota_customer",
    ),
    InstalledCapability(
        id="vehicle_context",
        kind=CapabilityKind.DATA,
        label="Vehicle context",
        description="Year/make/model/mileage for the customer's vehicle.",
    ),
    InstalledCapability(
        id="contract_lease_context",
        kind=CapabilityKind.DATA,
        label="Contract / lease context",
        description="Lease terms, months remaining, payment, buyout.",
    ),
    InstalledCapability(
        id="usage_data",
        kind=CapabilityKind.DATA,
        label="Usage data",
        description="Current vs historical mileage and change percent.",
    ),
    InstalledCapability(
        id="nessie_banking",
        kind=CapabilityKind.CONNECTION,
        label="Capital One Nessie",
        description="Banking connector; derived FinancialContext only — raw rows stay server-side.",
        action="nessie_load_customer_financials",
    ),
    InstalledCapability(
        id="uploaded_files",
        kind=CapabilityKind.CONNECTION,
        label="Uploaded files",
        description="Plan-time file excerpts via /api/attachments.",
    ),
    # Deterministic functions that exist in this codebase
    InstalledCapability(
        id="fn_evaluate_mobility_relevance",
        kind=CapabilityKind.FUNCTION,
        label="Evaluate mobility relevance",
        description="CODE gate: lease window + usage change → relevant or NO_ACTION.",
        action="evaluate_mobility_relevance",
    ),
    InstalledCapability(
        id="fn_build_financial_context",
        kind=CapabilityKind.FUNCTION,
        label="Build financial context",
        description="Derive safe budget, runway, stress from Nessie-shaped data.",
        action="build_financial_context",
    ),
    InstalledCapability(
        id="fn_calculate_affordability",
        kind=CapabilityKind.FUNCTION,
        label="Calculate affordability",
        description="CODE affordability / headroom signals from FinancialContext.",
        action="calculate_affordability",
    ),
    InstalledCapability(
        id="fn_calculate_emergency_runway",
        kind=CapabilityKind.FUNCTION,
        label="Calculate emergency runway",
        description="Included in FinancialContext derivation.",
        action="calculate_emergency_runway",
    ),
    InstalledCapability(
        id="fn_identify_financial_stress",
        kind=CapabilityKind.FUNCTION,
        label="Identify financial stress",
        description="LOW/MEDIUM/HIGH stress from reserves, debt burden, cashflow.",
        action="identify_financial_stress",
    ),
    InstalledCapability(
        id="fn_score_mobility_options",
        kind=CapabilityKind.FUNCTION,
        label="Score mobility options",
        description="Deterministic buy/upgrade/return/flexible scoring.",
        action="score_mobility_options",
    ),
    InstalledCapability(
        id="fn_rank_customers",
        kind=CapabilityKind.FUNCTION,
        label="Rank customers",
        description="Portfolio ranking for loan or mobility priority (CODE).",
        action="rank_customers",
    ),
    InstalledCapability(
        id="fn_load_toyota_portfolio",
        kind=CapabilityKind.FUNCTION,
        label="Load Toyota portfolio",
        description="Load all installed Toyota demo customers.",
        action="load_toyota_portfolio",
    ),
    # AI capabilities (judgment only)
    InstalledCapability(
        id="ai_synthesize_recommendation",
        kind=CapabilityKind.AI,
        label="Synthesize recommendation",
        description="LLM customer-facing explanation from structured summaries only.",
        action="synthesize_recommendation",
    ),
    InstalledCapability(
        id="ai_explain_shortlist",
        kind=CapabilityKind.AI,
        label="Explain shortlist",
        description="LLM shortlist narrative for loan/mobility portfolio results.",
        action="explain_shortlist",
    ),
    InstalledCapability(
        id="ai_review_recommendation",
        kind=CapabilityKind.AI,
        label="Review recommendation",
        description="Optional thorough LLM review against guardrails.",
        action="review_recommendation",
    ),
)

# Capabilities that are NOT installed — used for NEEDS_SETUP demos
NOT_INSTALLED: tuple[InstalledCapability, ...] = (
    InstalledCapability(
        id="ats",
        kind=CapabilityKind.CONNECTION,
        label="ATS / recruiting system",
        description="Applicant tracking system.",
        pack="recruiting",
        installed=False,
    ),
    InstalledCapability(
        id="email",
        kind=CapabilityKind.CONNECTION,
        label="Email send",
        description="Outbound email provider.",
        pack="communications",
        installed=False,
    ),
    InstalledCapability(
        id="calendar",
        kind=CapabilityKind.CONNECTION,
        label="Calendar scheduling",
        description="Calendar / interview scheduling API.",
        pack="communications",
        installed=False,
    ),
)


def catalog_dict() -> dict[str, Any]:
    return {
        "packs": [
            {
                "id": TOYOTA_PACK_ID,
                "name": "Toyota Mobility / Financial Decision Pack",
                "installed": True,
                "description": (
                    "Customer/vehicle/lease/usage context, Nessie-derived finance, "
                    "deterministic mobility & loan ranking, optional LLM synthesis."
                ),
            }
        ],
        "capabilities": [
            {
                "id": c.id,
                "kind": c.kind.value,
                "label": c.label,
                "description": c.description,
                "pack": c.pack,
                "action": c.action,
                "installed": c.installed,
            }
            for c in INSTALLED
        ],
        "not_installed_examples": [
            {"id": c.id, "label": c.label, "pack": c.pack} for c in NOT_INSTALLED
        ],
    }


def installed_action_ids() -> set[str]:
    return {c.action for c in INSTALLED if c.action and c.installed}


def installed_connection_ids() -> set[str]:
    return {c.id for c in INSTALLED if c.kind == CapabilityKind.CONNECTION and c.installed}
