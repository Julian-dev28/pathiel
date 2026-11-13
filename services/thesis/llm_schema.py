"""ThesisDraft: the ONLY JSON shape the LLM is ever asked to produce.

contracts/strategies/models.py's own module docstring draws the line:
"numbers are computed in DeskAnalytics/IntelPacket (deterministic space).
The Thesis may only quote numbers that exist in those payloads." And further
down: "The LLM returns JSON for the latent fields only: call, scenarios with
return buckets, bull and bear points citing source ids, what changes my
mind, lessons, and a reason per leverage rung. Code fills everything
deterministic: ids, timestamps, expiry, catalysts copied from intel,
leverage verdicts copied from the analytics ladder, sources resolved from
cited ids, sha256 refs of both payloads, usage."

Call / Scenario / Point / Lesson are imported directly from the contract,
not re-declared here, so this schema can never silently drift from it - a
contract change that adds a required field on any of them is a type error
here, not a shape that quietly stops matching.

``leverage_reasons`` is this service's OWN shape, not a contract type: one
{leverage, reason} per ladder leverage, for the side of the call. Code fills
the contract's LeverageView by matching each reason to the ladder rung with
that leverage AND the call's side, and copies the verdict from the rung. It
used to be a bare list of strings aligned by position; live on 2026-10-17 the
model ordered them differently and every reason landed on the wrong rung.
"""
from __future__ import annotations

from typing import Any, List

from pydantic import BaseModel, ConfigDict, Field

from contracts.strategies.models import Call, Lesson, Point, Scenario

# One lesson per thesis: the system prompt asks for "one plain-English
# lesson tied to today's market" (singular, deliberately - Julien has ADHD
# and the desk-ui contract wants one decision per screen, not a syllabus).
_MIN_LESSONS = 1
_MAX_LESSONS = 1

_MIN_SCENARIOS = 3
_MAX_SCENARIOS = 5


class LeverageReason(BaseModel):
    model_config = ConfigDict(extra="forbid")

    leverage: float = Field(description="One of the ladder leverages, e.g. 5, 10, 20.")
    reason: str = Field(description="Why this leverage fits or not, for the side of YOUR call, "
                                    "quoting that rung's numbers.")


class ThesisDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")

    call: Call
    scenarios: List[Scenario] = Field(min_length=_MIN_SCENARIOS, max_length=_MAX_SCENARIOS)
    bull: List[Point] = Field(min_length=1, max_length=4)
    bear: List[Point] = Field(min_length=1, max_length=4)
    what_changes_my_mind: List[str] = Field(min_length=1, max_length=4)
    lessons: List[Lesson] = Field(min_length=_MIN_LESSONS, max_length=_MAX_LESSONS)
    leverage_reasons: List[LeverageReason] = Field(min_length=1)


# Per-field prose caps injected into the generated schema (see
# thesis_draft_json_schema below). Real eval run, 2026-10-17: with NO length
# caps beyond the contract's own (one_liner<=160, Lesson.explanation<=400),
# a single call's output ran 3100-4000 tokens against a 1800-token budget -
# the model was thorough, not hallucinating, just unbounded. --json-schema
# enforces maxLength exactly the way it enforces minItems/maxItems (verified
# live for the array bounds; same structured-output mechanism), so this is a
# structural fix, not a prompt plea the model can ignore.
_PROSE_CAPS: dict[str, int] = {
    "invalidation": 200,   # Call - not capped by the contract itself
    "text": 180,           # Point.text (bull/bear)
    "description": 220,    # Scenario.description
    "tied_to": 200,        # Lesson.tied_to - not capped by the contract itself
}
_WHAT_CHANGES_MY_MIND_ITEM_MAX_LENGTH = 120
_LEVERAGE_REASON_MAX_LENGTH = 160


def thesis_draft_json_schema(n_leverage_rungs: int) -> dict[str, Any]:
    """JSON Schema for ``services.llm.complete(json_schema=...)``.

    Pydantic emits ``$defs``/``$ref`` for the nested Call/Scenario/Point/
    Lesson models - confirmed live 2026-10-17 that the Claude Code CLI's
    ``--json-schema`` handles nested ``$defs``/``$ref`` correctly (see
    services/llm/README.md). ``n_leverage_rungs`` is known for this specific
    call (``len(analytics.ladder)``), so the schema pins
    ``leverage_reasons`` to exactly that many items instead of leaving it
    open-ended - the model cannot return the wrong count even before the
    validator runs.

    Also injects maxLength caps (see _PROSE_CAPS) onto free-text fields the
    underlying contract models (Call, Scenario, Point, Lesson - not owned by
    this service) do not themselves bound. This mutates only the SCHEMA
    object handed to the CLI, never contracts.strategies.models itself.
    """
    schema = ThesisDraft.model_json_schema()
    n = max(1, int(n_leverage_rungs))
    schema["properties"]["leverage_reasons"]["minItems"] = n
    schema["properties"]["leverage_reasons"]["maxItems"] = n
    schema["$defs"]["LeverageReason"]["properties"]["reason"]["maxLength"] = _LEVERAGE_REASON_MAX_LENGTH
    schema["properties"]["what_changes_my_mind"]["items"]["maxLength"] = _WHAT_CHANGES_MY_MIND_ITEM_MAX_LENGTH

    defs = schema.get("$defs", {})
    for def_name, def_schema in defs.items():
        props = def_schema.get("properties", {})
        for field_name, cap in _PROSE_CAPS.items():
            if field_name in props and props[field_name].get("type") == "string":
                props[field_name]["maxLength"] = cap
    return schema
