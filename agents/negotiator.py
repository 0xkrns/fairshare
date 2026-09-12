"""AGENT 2 -- Negotiator. Decides a FAIR split, not an equal one.

The environment is what makes this possible: the agent knows who is in the
group, and can ask the group one targeted question with tappable buttons
instead of guessing. A web form cannot ask a follow-up.
"""
from llm import json_call

DRINK_WORDS = ("wine", "beer", "cocktail", "drink", "whisky", "whiskey", "vodka", "gin", "rum")
SHARED_CHARGE_WORDS = ("tax", "tip", "service", "gst", "vat", "delivery", "discount")

SYSTEM = """You are a fair-split agent for a group of friends.
Given receipt line items and the group's members, decide how to split.

Rules:
- Genuinely shared things (mains everyone picked at, tax, tip, service) split evenly.
- Individually-consumed things (a bottle of wine, one dessert, alcohol, a
  specific main) should NOT be split evenly if it is plausible only some
  people had them.
- Ask EXACTLY ONE clarifying question whenever a receipt has an item that may
  not have been shared. Ask about the single highest-value ambiguous item.
  Prefer drinks and alcohol, because friends often do not share those equally.
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
    # Asking the group is a core product interaction, not an optional model
    # flourish. If the model skips it, make the highest-value plausible item
    # explicit so friends can tell us who consumed it.
    if len(member_names) > 1 and not out.get("question"):
        candidate = clarification_candidate(receipt)
        if candidate:
            out["question"] = {
                "item": candidate["name"],
                "cents": candidate["cents"],
                "text": f"Who had {candidate['name']} (${candidate['cents'] / 100:.2f})?",
            }
    out["split"] = _force_sum(out.get("split", {}), int(receipt.get("total_cents", 0)), payer)
    return out


def clarification_candidate(receipt):
    """Choose one consumable that merits a group answer, deterministically."""
    candidates = selectable_items(receipt)
    if not candidates:
        return None
    is_drink, cents, name = max(candidates, key=lambda item: (item[0], item[1], item[2]))
    return {"name": name, "cents": cents}


def selectable_items(receipt):
    """Return items people may need to claim individually.

    Tax, tips, and other group charges are deliberately excluded from the
    Telegram checklist: they are apportioned across the group automatically.
    """
    candidates = []
    for item in receipt.get("items", []):
        name = str(item.get("name", "")).strip()
        cents = int(item.get("cents", 0) or 0)
        lower_name = name.lower()
        if not name or cents <= 0 or any(word in lower_name for word in SHARED_CHARGE_WORDS):
            continue
        # Explicitly shared items should not trigger an unnecessary question.
        if item.get("shared") is True:
            continue
        candidates.append((any(word in lower_name for word in DRINK_WORDS), cents, name))
    return candidates


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
