"""AGENT 3 -- Dispute mediator. The one that only works inside a chat.

When someone says "I didn't order that", the evidence is the receipt AND the
surrounding conversation. This agent reads both, proposes a compromise, and
appends a correcting adjustment rather than rewriting history.
"""
from llm import json_call

SYSTEM = """You are a neutral mediator settling a disagreement about a bill
split between friends. You have the receipt, the current split, and the recent
group conversation.

Act like an arbitrator, not a calculator:
- Weigh what people actually said. A specific claim ("I don't drink") outweighs
  a vague one.
- Where evidence is genuinely balanced, split the contested amount rather than
  picking a winner.
- Protect the friendship: never accuse anyone of lying. Say the evidence is
  unclear.
- Explain your reasoning in two sentences a friend would accept.
- Keep everyone else's share unchanged unless the contested amount forces it."""

HINT = """Schema:
{"contested_item": str, "contested_cents": int,
 "new_split": {"<name>": <cents int>},
 "reasoning": str, "confidence": "high"|"medium"|"low"}
new_split must sum to the same total as the current split."""


def mediate(receipt, current_split, transcript, complaint):
    user = (
        f"Receipt items: {receipt.get('items')}\n"
        f"Current split (cents): {current_split}\n"
        f"Recent group conversation:\n{transcript}\n\n"
        f"The disagreement: {complaint}"
    )
    out = json_call(SYSTEM, user, schema_hint=HINT)
    if "_error" in out:
        return out
    total = sum(current_split.values())
    ns = {k: int(round(float(v))) for k, v in out.get("new_split", {}).items()}
    if ns:
        drift = total - sum(ns.values())
        if drift:
            ns[max(ns, key=ns.get)] += drift
    out["new_split"] = ns
    return out
