"""Thin OpenAI wrapper. Every agent goes through here so model choice,
JSON enforcement and failure handling live in exactly one place."""
import base64
import json
import os

from openai import OpenAI

_client = None


def client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["OPENAI_API_KEY"])
    return _client


VISION_MODEL = os.getenv("VISION_MODEL", "gpt-4o")
REASON_MODEL = os.getenv("REASON_MODEL", "gpt-4o")


def json_call(system, user, model=None, image_bytes=None, schema_hint=""):
    """Always returns a dict. Never raises into the bot loop."""
    content = [{"type": "text", "text": user}]
    if image_bytes:
        b64 = base64.b64encode(image_bytes).decode()
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{b64}"}}
        )
    try:
        r = client().chat.completions.create(
            model=model or (VISION_MODEL if image_bytes else REASON_MODEL),
            response_format={"type": "json_object"},
            messages=[
                {"role": "system", "content": system + "\n\nReturn ONLY JSON. " + schema_hint},
                {"role": "user", "content": content},
            ],
            temperature=0.2,
        )
        return json.loads(r.choices[0].message.content)
    except Exception as exc:  # noqa: BLE001
        return {"_error": str(exc)}


def text_call(system, user, model=None, temperature=0.7):
    try:
        r = client().chat.completions.create(
            model=model or REASON_MODEL,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=temperature,
        )
        return r.choices[0].message.content.strip()
    except Exception as exc:  # noqa: BLE001
        return f"(agent unavailable: {exc})"
