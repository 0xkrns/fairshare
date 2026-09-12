# FairShare

Expense apps are good at *recording* who owes what. They are bad at every
human part around it: getting the receipt in, deciding what's **fair** rather
than equal, arguing about it, and chasing people without being awkward.

Those parts all happen in one place — **the group chat** — so that is where
FairShare lives. Drop a receipt photo into the group and five agents take over.

```
        📸 receipt photo dropped in the group
                     │
         ┌───────────▼────────────┐
         │  1. PARSER   (vision)  │  line items → structured JSON
         └───────────┬────────────┘
         ┌───────────▼────────────┐
         │  2. NEGOTIATOR         │  fair ≠ equal. Asks the group ONE
         │     (fair split)       │  question with tappable buttons
         └───────────┬────────────┘
                     │            ◄── group taps "that was me"
         ┏━━━━━━━━━━━▼━━━━━━━━━━━━┓
         ┃   APPEND-ONLY LEDGER   ┃  every agent reads/writes here
         ┃   (SQLite event log)   ┃  balances = pure fold over events
         ┗━━━━━━━━━━━┳━━━━━━━━━━━━┛
          ┌──────────┼───────────────────┐
   ┌──────▼─────┐ ┌──▼──────────────┐ ┌──▼──────────┐
   │ 3. MEDIATOR│ │ 4. SETTLER      │ │ 5. NUDGE    │
   │  arbitrates│ │  exact netting  │ │  unprompted │
   │  disputes  │ │  + "worth it?"  │ │  reminder   │
   └────────────┘ └─────────────────┘ └─────────────┘
```

## The five agents

| # | Agent | What it does | Why it needs the chat |
|---|-------|--------------|----------------------|
| 1 | **Parser** | Receipt photo → itemised JSON (`agents/parser.py`) | The photo is *already* being dropped in the group. No upload, no form. |
| 2 | **Negotiator** | Decides a **fair** split — someone who didn't drink doesn't pay for the wine (`agents/negotiator.py`) | It knows the group's members, and can **ask one follow-up question** with tap-to-answer buttons. A web form can't ask. |
| 3 | **Mediator** | "I didn't order that" → reads the receipt **and the conversation**, proposes a compromise, explains itself (`agents/mediator.py`) | The evidence for a dispute *is* the chat history. This agent is impossible outside the environment. |
| 4 | **Settler** | Minimum-transaction debt netting, then a verdict on whether it's even worth settling (`agents/settler.py`) | — |
| 5 | **Nudge** | Wakes up on its own, writes the awkward reminder in the group's own tone (`agents/nudge.py`) | Delivers into the conversation, matched to how that group talks. |

## Two deliberate design decisions

**1. The LLM never touches money arithmetic.** Debt netting is a
deterministic greedy match (`settler.simplify`), splits are forced to sum
exactly (`negotiator._force_sum`), and the parser trusts its own sum over the
model's stated total. Run `python verify.py` — ten assertions on the money math.

**2. Disputes append, they don't overwrite.** The mediator writes a *delta*
event, so the original split stays in the log and the ledger always folds to
zero. Auditability is the point: friends want to see what changed.

## Not Splitwise

Splitwise settles every debt to zero, exactly. FairShare has an opinion:
asking a friend to transfer $3 is worse than eating it. `/settle` prunes
trivial transfers and tells you when a group is "basically even over the last
three trips — don't bother."

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env      # add TELEGRAM_BOT_TOKEN + OPENAI_API_KEY
python bot.py
```

**Telegram setup (2 minutes):**
1. `/newbot` to [@BotFather](https://t.me/BotFather) → copy the token.
2. **`/setprivacy` → your bot → `Disable`.** ⚠️ Without this the bot cannot see
   group messages and agents 3 and 5 will silently do nothing.
3. Add the bot to a group. Send `/help`.

| Action | Agent triggered |
|---|---|
| send a receipt photo | Parser → Negotiator |
| tap a name button | Negotiator (resolution) |
| reply "I didn't order the wine" (or `/dispute ...`) | Mediator |
| `/settle` | Settler |
| `/nudge` / `/nudge 20` | Nudge (the `20` version fires on its own) |

## Judging criteria → where to look

- **Core requirements & functionality** — complete loop: photo → itemise →
  clarify → ledger → dispute → settle → chase. Nothing is a stub.
- **Innovation & theme alignment** — the chat isn't a UI skin. It supplies the
  receipt, the social graph (who's in the group = who's splitting), the dispute
  evidence, and the delivery channel. Agent 3 cannot exist elsewhere.
- **Technical execution** — append-only event log, agents isolated behind one
  LLM boundary (`llm.py`), JSON-schema-constrained calls, arithmetic kept out
  of the model, `verify.py`, and a poll loop that survives any agent failure.
- **Usefulness & agentic experience** — one tap resolves an ambiguous split;
  Agent 5 acts with no human prompt.

## Sponsor integrations

- **OpenAI** — vision (parsing) and reasoning (all four other agents).
- **Trigger.dev** — swap `nudge.schedule()` for a durable scheduled task so
  reminders survive a restart. One-function change.
- **OpenRouter** — set `REASON_MODEL` and point `llm.py`'s base URL to route
  or fall back across models.
- **ClickHouse** — the event log is already append-only; it's a drop-in sink.
