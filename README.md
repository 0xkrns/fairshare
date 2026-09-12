# FairShare

Expense apps are good at recording who owes what. They are bad at every
human part around it: getting the receipt in, deciding what's **fair** rather
than equal, arguing about it, and chasing people without being awkward.

Those parts all happen in one place, the group chat, so that is where
FairShare lives. Drop a receipt photo into the group and the agents take over.

```
        📸 receipt photo dropped in the group
                     │
         ┌───────────▼────────────┐
         │  1. PARSER   (vision)  │  line items → structured JSON
         └───────────┬────────────┘
         ┌───────────▼────────────┐
         │  2. NEGOTIATOR         │  fair ≠ equal. Asks the group ONE
         │     (fair split)       │  item checklist in the group chat
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
| 2 | **Negotiator** | Decides a **fair** split — someone who didn't drink doesn't pay for the wine (`agents/negotiator.py`) | It shows only individually claimable items. Selections toggle visibly, and people can revise them before the final split posts. Send `/finalize` to have the bot ask when future splits should publish. |
| 3 | **Mediator** | "I didn't order that" → reads the receipt **and the conversation**, asks one follow-up about the disputed item, then proposes a compromise with reasoning (`agents/mediator.py`) | The evidence for a dispute *is* the chat history; the group must approve the proposal before it changes the ledger. |
| 4 | **Settler** | Minimum-transaction debt netting, then a verdict on whether it's even worth settling (`agents/settler.py`) | — |
| 5 | **Nudge** | Wakes up on its own, writes the awkward reminder in the group's own tone (`agents/nudge.py`) | Delivers into the conversation, matched to how that group talks. |

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
three trips, don't bother."

## Run it

```bash
pip install -r requirements.txt
cp .env.example .env      # add TELEGRAM_BOT_TOKEN + OPENROUTER_API_KEY
python bot.py
```

| Action | Agent triggered |
|---|---|
| send a receipt photo | Parser → Negotiator |
| tap every item you had, then “I’m done selecting” | Negotiator (resolution after every member responds or the timeout) |
| reply "I didn't order the wine" (or `/dispute ...`) | Mediator proposes; group applies or keeps the original |
| `/settle` | Settler |
| `/nudge` / `/nudge 20` | Nudge (the `20` version fires on its own while the bot stays online) |
