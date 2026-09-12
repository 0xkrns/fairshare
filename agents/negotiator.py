"""AGENT 2 -- Negotiator. Decides a FAIR split, not an equal one.

The environment is what makes this possible: the agent knows who is in the
group, and can ask the group one targeted question with tappable buttons
instead of guessing. A web form cannot ask a follow-up.
"""
from llm import json_call

SYSTEM = """You are a fair-split agent for a group of friends.
Given receipt line items and the group's members, decide how to split.

Rules:
- Genuinely shared things (mains everyone picked at, tax, tip, service) split evenly.
- Individually-consumed things (a bottle of wine, one dessert, alcohol, a
  specific main) should NOT be split evenly if it is plausible only some
  people had them.
- You may ask AT MOST ONE clarifying question, and only about the single
  highest-value ambiguous item. Prefer assuming even split over interrogating.
- Fairness beats precision. Do not ask about a $3 coffee."""

HINT = """Schema:
{"question": null | {"item": str, "cents": int,
                     "text": "Who had the bottle of wine ($48)?"},
 "assumptions": [str],
 "split": {"<member name>": <cents int>}}
The split must sum to total_cents exactly. Put any rounding remainder on the payer."""


def propose(receipt, member_names, payer):
    user = (
        f"Members: {member_names}\nPayer: {payer}\n"
        f"Total (cents): {receipt.get('total_cents')}\n"
        f"Items: {receipt.get('items')}"
    )
    out = json_call(SYSTEM, user, schema_hint=HINT)
    if "_error" in out:
        return out
    out["split"] = _force_sum(out.get("split", {}), int(receipt.get("total_cents", 0)), payer)
    return out


def _force_sum(split, total, payer):
    """Never let the LLM's arithmetic reach the ledger."""
    split = {k: int(round(float(v))) for k, v in split.items() if v is not None}
    if not split:
        return {}
    drift = total - sum(split.values())
    if drift:
        target = payer if payer in split else next(iter(split))
        split[target] += drift
    return split
