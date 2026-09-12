"""Pre-demo preflight. Run this instead of guessing: python preflight.py

Checks the two keys AND whether Telegram group privacy mode is still on --
which is the single most common reason a working bot appears to do nothing.
"""
import os
import sys

try:
    import requests
except ImportError:
    sys.exit("X  requests not installed. Run: pip install -r requirements.txt")

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    sys.exit("X  python-dotenv not installed. Run: pip install -r requirements.txt")

OK, BAD, WARN = "OK  ", "X   ", "!   "
fail = False


def say(mark, msg):
    global fail
    if mark is BAD:
        fail = True
    print(mark + msg)


# ------------------------------------------------------------ telegram
tok = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
if not tok or tok.startswith("123456:"):
    say(BAD, "TELEGRAM_BOT_TOKEN missing (still the placeholder?). Get one from @BotFather.")
else:
    try:
        r = requests.get(f"https://api.telegram.org/bot{tok}/getMe", timeout=15).json()
    except Exception as e:
        r = {"ok": False, "description": str(e)}
    if not r.get("ok"):
        say(BAD, f"Telegram rejected the token: {r.get('description')}")
    else:
        me = r["result"]
        say(OK, f"Telegram token valid — bot is @{me['username']}")

        # THE check. getMe reports this flag directly.
        if me.get("can_read_all_group_messages"):
            say(OK, "Group privacy mode is OFF — agents can read group messages.")
        else:
            say(BAD, "PRIVACY MODE IS STILL ON. The bot will only see /commands, "
                     "so the Mediator and Nudge agents will silently do nothing.")
            print("       Fix: @BotFather -> /setprivacy -> pick your bot -> Disable")
            print("       Then REMOVE the bot from your group and re-add it (required).")

        if not me.get("supports_inline_queries", True):
            pass  # not needed; inline keyboards work regardless

# -------------------------------------------------------------- model api
key = (os.getenv("OPENROUTER_API_KEY") or os.getenv("OPENAI_API_KEY") or "").strip()
base = (os.getenv("OPENROUTER_BASE_URL") or os.getenv("OPENAI_BASE_URL") or "https://openrouter.ai/api/v1").strip().rstrip("/")
provider = "OpenRouter" if "openrouter" in base else "OpenAI"

if not key or key in ("sk-...", "PASTE_YOUR_OPENROUTER_KEY_HERE"):
    say(BAD, f"{provider} API key missing (still the placeholder?).")
elif provider == "OpenRouter" and not key.startswith("sk-or-"):
    say(WARN, "Key doesn't look like an OpenRouter key (expected sk-or-v1-...).")

if key and key not in ("sk-...", "PASTE_YOUR_OPENROUTER_KEY_HERE"):
    try:
        r = requests.get(
            f"{base}/models",
            headers={"Authorization": f"Bearer {key}"},
            timeout=25,
        )
        if r.status_code == 200:
            ids = {m.get("id") for m in r.json().get("data", [])}
            say(OK, f"{provider} key valid ({len(ids)} models available).")
            for label, env, default in (
                ("vision", "VISION_MODEL", "gpt-4o"),
                ("reasoning", "REASON_MODEL", "gpt-4o"),
            ):
                m = os.getenv(env, default)
                if not ids:
                    say(WARN, f"couldn't list models; can't confirm '{m}'.")
                elif m in ids:
                    say(OK, f"{label} model '{m}' available.")
                else:
                    hint = ""
                    if provider == "OpenRouter" and "/" not in m:
                        hint = f" OpenRouter needs a prefix — try 'openai/{m}'."
                    say(BAD, f"{label} model '{m}' not available on {provider}.{hint}")
        elif r.status_code == 401:
            say(BAD, f"{provider} rejected the key (401). Wrong or revoked.")
        elif r.status_code == 402:
            say(BAD, f"{provider} says out of credit (402). Top up at openrouter.ai/settings/credits")
        else:
            say(WARN, f"{provider} returned {r.status_code}.")
    except Exception as e:
        say(WARN, f"Couldn't reach {provider}: {e}")

if not os.getenv("OPENROUTER_API_KEY"):
    say(BAD, "llm.py requires OPENROUTER_API_KEY specifically -- add it to .env.")

# --------------------------------------------------------------- hygiene
if os.path.exists(".env"):
    import subprocess
    try:
        tracked = subprocess.run(["git", "ls-files", ".env"], capture_output=True, text=True).stdout
        if tracked.strip():
            say(BAD, "DANGER: .env is tracked by git. Run: git rm --cached .env")
        else:
            say(OK, ".env is not tracked by git.")
    except Exception:
        pass

print()
print("NOT READY — fix the X lines above." if fail else "All checks passed. Run: python bot.py")
sys.exit(1 if fail else 0)
