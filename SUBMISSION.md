# Submission — FairShare

**Event:** AI Tinkerers "Agents, Everywhere" — Singapore, 12 Sep 2026
**Surface:** Telegram group chat (conversation platform)
**Repo:** https://github.com/0xkrns/fairshare

## The one complete interaction

A group of friends splits a restaurant bill without leaving the chat they were
already arguing in:

1. Someone drops a **receipt photo** into the group.
2. The **Parser** agent itemises it (vision).
3. The **Negotiator** agent proposes a *fair* split — not an equal one — and
   asks the group **one** clarifying question about the highest-value ambiguous
   item, with tap-to-answer buttons.
4. A member taps their name; the split is committed to the ledger.
5. A member replies *"I didn't order the wine, I don't drink."* The **Mediator**
   agent reads the receipt **and the surrounding conversation**, proposes a
   revised split, and explains its reasoning.
6. `/settle` — the **Settler** agent computes minimum-transaction netting and
   gives a verdict on whether settling is even worth it.
7. The **Nudge** agent later fires **with no human prompt** and writes the
   awkward reminder in the group's own tone.

Verified end-to-end in a live Telegram group.

## Why this surface matters (theme alignment)

The group chat is not a UI skin over an expense tracker. It supplies:

- the **receipt** (the photo is already being dropped there),
- the **social graph** — who is in the group *is* who is splitting,
- the **dispute evidence** — the argument about a bill happens in messages, so
  the Mediator's input is the conversation itself,
- the **delivery channel** for an unprompted reminder, in the group's register.

The Mediator agent cannot exist outside a conversational environment. That is
the core of the submission.

## Created during the event

All application code in this repository was written during the hackathon:

- `bot.py` — Telegram Bot API long-polling loop and agent orchestration
- `ledger.py` — append-only SQLite event log; balances are a pure fold
- `llm.py` — single model boundary (JSON-constrained calls, failure isolation)
- `agents/parser.py` — receipt vision → structured line items
- `agents/negotiator.py` — fair-split reasoning + one-question clarification
- `agents/mediator.py` — dispute arbitration over receipt + chat transcript
- `agents/settler.py` — deterministic debt netting + "worth settling?" verdict
- `agents/nudge.py` — autonomous, tone-matched payment reminder
- `verify.py` — 10 assertions on the money arithmetic
- `README.md`, `DEMO.md`, `SUBMISSION.md`

No pre-existing project was resubmitted. The starter kit was not used.

## Inherited code and dependencies

Third-party libraries only, used as published (see `requirements.txt`):

| Dependency | Licence | Used for |
|---|---|---|
| `requests` | Apache-2.0 | Telegram Bot API calls |
| `openai` | Apache-2.0 | vision + reasoning calls |
| `python-dotenv` | BSD-3-Clause | local config loading |

No code was copied from the starter kit, from Splitwise, or from any other
project. `sqlite3`, `threading`, `collections` and `re` are Python standard
library.

## Sponsor integrations

- **OpenAI** — vision (receipt parsing) and reasoning (all four other agents).
  Model choice is configurable via `VISION_MODEL` / `REASON_MODEL`.
- **OpenRouter** — supported by pointing `llm.py`'s client base URL at
  OpenRouter and setting the model env vars.

## Engineering notes for judges

**The LLM never performs money arithmetic.** Debt netting is a deterministic
greedy match (`settler.simplify`), splits are forced to sum exactly
(`negotiator._force_sum`), and the parser trusts its own sum over the model's
stated total. Run `python verify.py` — no API key required.

**Disputes append, they never overwrite.** The Mediator writes a *delta* event,
so the original split survives in the log and balances always fold to zero.
A friend can audit exactly what changed and why.

**The bot survives agent failure.** Every handler is wrapped; a failed vision
call or malformed model response posts an error into the chat and the poll loop
continues.
