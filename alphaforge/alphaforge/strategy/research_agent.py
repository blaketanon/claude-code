"""Claude-powered research agent: proposes the next experiments for the autopilot.

Given the registry's recent results (metrics, feature importances, drift stats) it returns
a ranked list of concrete, machine-executable experiment configs. The autopilot validates
every proposal against the TrainingConfig schema, runs it under the same walk-forward
harness, and only promotes on statistically significant improvement.
"""
from __future__ import annotations

import json
from typing import Any

from pydantic import BaseModel, Field

from alphaforge.config import get_settings
from alphaforge.features import DEFAULT_FEATURE_SET
from alphaforge.models.base import available_model_types


class ExperimentProposal(BaseModel):
    hypothesis: str = Field(description="One-sentence, falsifiable hypothesis")
    model_type: str
    feature_set: list[str]
    hyperparams: dict[str, Any] = Field(default_factory=dict)
    label: dict[str, float] = Field(default_factory=dict, description="pt_mult, sl_mult, max_holding overrides")
    policy: dict[str, float] = Field(default_factory=dict, description="min_confidence, max_weight, target_vol overrides")
    expected_effect: str


class ResearchPlan(BaseModel):
    diagnosis: str
    proposals: list[ExperimentProposal]


class ResearchAgent:
    def __init__(self, model: str | None = None):
        import anthropic

        s = get_settings()
        if not s.anthropic_api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")
        self.client = anthropic.Anthropic(api_key=s.anthropic_api_key)
        self.model = model or s.llm_model

    def propose(self, champion_summary: dict, recent_experiments: list[dict], drift: dict, n: int = 3) -> ResearchPlan:
        system = (
            "You are a quantitative researcher improving an intraday equity ML strategy. "
            "Propose experiments that are small, testable and grounded in the evidence given. "
            f"Allowed model_type values: {available_model_types()}. "
            f"Allowed features (choose subsets): {DEFAULT_FEATURE_SET}. "
            "Prefer changes that address the diagnosis (e.g. overfitting -> stronger regularization or fewer features; "
            "regime drift -> shorter labels or vol-adaptive barriers; poor calibration -> higher min_confidence)."
        )
        user = json.dumps({"champion": champion_summary, "recent_experiments": recent_experiments[-10:], "feature_drift": drift, "n_proposals": n}, default=str, indent=1)
        resp = self.client.messages.parse(
            model=self.model,
            max_tokens=8000,
            system=system,
            messages=[{"role": "user", "content": user}],
            output_format=ResearchPlan,
        )
        if resp.stop_reason == "refusal" or resp.parsed_output is None:
            raise RuntimeError(f"research agent returned no output (stop_reason={resp.stop_reason})")
        plan = resp.parsed_output
        allowed = set(DEFAULT_FEATURE_SET)
        types = set(available_model_types())
        plan.proposals = [p for p in plan.proposals if p.model_type in types and set(p.feature_set) <= allowed and len(p.feature_set) >= 5][:n]
        return plan
