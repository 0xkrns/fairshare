"""AGENT 1 -- Parser. Receipt photo -> structured line items.

Lives in the group chat because that is where the photo already gets
dropped. No upload step, no app, no "add expense" form.
"""
from llm import json_call

SYSTEM = """You are a receipt parsing agent. Extract every line item from the
receipt image. Be precise with numbers. Convert all money to integer cents.
If a line is a tax, tip or service charge, mark it shared=true so it can be
apportioned rather than assigned to one person."""

HINT = """Schema:
{"merchant": str, "currency": str, "total_cents": int,
 "items": [{"name": str, "qty": int, "cents": int, "shared": bool}],
 "confidence": float}"""


def parse(image_bytes):
    out = json_call(SYSTEM, "Itemise this receipt.", image_bytes=image_bytes, schema_hint=HINT)
    if "_error" in out:
        return out
    items = out.get("items", [])
    # Trust the arithmetic of the sum over the model's stated total.
    computed = sum(int(i.get("cents", 0)) for i in items)
    out.setdefault("total_cents", computed)
    out["computed_total_cents"] = computed
    out["total_mismatch"] = abs(computed - int(out["total_cents"])) > 100
    return out
