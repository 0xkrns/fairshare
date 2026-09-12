"""FairShare -- a bill-splitting agent swarm that lives in the group chat.

Deliberately uses the raw Telegram Bot API over `requests` with long polling:
no webhook, no ngrok, no public URL, nothing to break during a live demo.

Run:  python bot.py
"""
import os
import re
import threading
import time
from collections import defaultdict, deque
from datetime import datetime, timedelta

import requests
from dotenv import load_dotenv

load_dotenv()  # must run before agent imports read model names from env

import ledger
from agents import mediator, negotiator, nudge, parser, settler
from llm import json_call
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"

# Rolling conversation context per chat -- this is the raw material the
# mediator and nudge agents reason over. The environment IS the data source.
TRANSCRIPT = defaultdict(lambda: deque(maxlen=40))
# chat_id -> a pending split clarification or mediation awaiting group approval
PENDING = {}
SELECTION_TIMEOUT_SECONDS = int(os.getenv("SELECTION_TIMEOUT_SECONDS", "90"))
# Per-chat delay for new receipts; set with /finalize <minutes|hours|eod>.
FINALIZE_DELAYS = {}


# ------------------------------------------------------------- telegram io

def send(chat_id, text, keyboard=None, reply_to=None):
    payload = {"chat_id": chat_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    if reply_to:
        payload["reply_to_message_id"] = reply_to
    r = requests.post(f"{API}/sendMessage", json=payload, timeout=20)
    if not r.ok:
        print("send failed:", r.text)
    return r.json()


def typing(chat_id):
    requests.post(f"{API}/sendChatAction", json={"chat_id": chat_id, "action": "typing"}, timeout=10)


def download_photo(photo_sizes):
    file_id = photo_sizes[-1]["file_id"]  # largest
    meta = requests.get(f"{API}/getFile", params={"file_id": file_id}, timeout=20).json()
    path = meta["result"]["file_path"]
    return requests.get(f"{FILE_API}/{path}", timeout=40).content


def answer_callback(cb_id, text=""):
    requests.post(
        f"{API}/answerCallbackQuery", json={"callback_query_id": cb_id, "text": text}, timeout=10
    )


def edit_message(chat_id, message_id, text, keyboard=None):
    payload = {"chat_id": chat_id, "message_id": message_id, "text": text, "parse_mode": "Markdown"}
    if keyboard:
        payload["reply_markup"] = {"inline_keyboard": keyboard}
    requests.post(f"{API}/editMessageText", json=payload, timeout=20)


def name_of(user):
    return user.get("first_name") or user.get("username") or str(user["id"])


def transcript_text(chat_id):
    return "\n".join(TRANSCRIPT[chat_id]) or "(no conversation yet)"


def selection_keyboard(pend):
    rows = [
        [{"text": f"{item['name']} — {ledger.money(item['cents'])}",
          "callback_data": f"items:select:{index}"}]
        for index, item in enumerate(pend["items"])
    ]
    rows.append([
        {"text": "I'm done selecting", "callback_data": "items:done"},
        {"text": "Change my selection", "callback_data": "items:change"},
    ])
    rows.append([{"text": "Cancel", "callback_data": "items:cancel"}])
    return rows


def selection_text(pend):
    lines = ["*Which items did you have?*", "Tap every item you consumed; tap it again to remove it."]
    for user_id, name in pend["participants"].items():
        selected = [pend["items"][index]["name"] for index, claimers in pend["claims"].items() if name in claimers]
        status = "✓ done" if user_id in pend["responded"] else "selecting"
        lines.append(f"• {name} ({status}): " + (", ".join(selected) if selected else "nothing selected"))
    lines.append(pend["finalize_note"])
    return "\n".join(lines)


def refresh_selection_message(chat_id, pend):
    if pend.get("message_id"):
        edit_message(chat_id, pend["message_id"], selection_text(pend), selection_keyboard(pend))


def parse_finalize_delay(value):
    """Return a delay in seconds for /finalize now, 30m, 2h, or eod."""
    value = value.strip().lower()
    if value in ("now", "instant"):
        return 0
    if value in ("eod", "end-of-day", "end of day"):
        now = datetime.now().astimezone()
        end = now.replace(hour=23, minute=59, second=0, microsecond=0)
        return max(60, int((end - now).total_seconds()))
    match = re.fullmatch(r"(\d+)(m|h)", value)
    if not match:
        return None
    amount, unit = int(match.group(1)), match.group(2)
    return amount * (60 if unit == "m" else 3600)


def finalize_time_text(delay):
    if not delay:
        return "The final split will publish when everyone is done, or when the selection window closes."
    when = datetime.now().astimezone() + timedelta(seconds=delay)
    return "*Final split will be published at %s.*" % when.strftime("%I:%M %p").lstrip("0")


def set_finalize_delay(chat_id, delay, label):
    FINALIZE_DELAYS[chat_id] = delay
    if delay:
        return send(chat_id, f"Future receipt splits are scheduled for {label}. The bot will simply publish at that time.")
    return send(chat_id, "Future receipt splits will publish after everyone finishes, or when the selection window closes.")


def finalize_keyboard():
    return [
        [{"text": "Start now", "callback_data": "schedule:now"}],
        [{"text": "In 30 minutes", "callback_data": "schedule:30m"},
         {"text": "In 2 hours", "callback_data": "schedule:2h"}],
        [{"text": "End of day", "callback_data": "schedule:eod"}],
    ]


def begin_participation(chat_id, pending_id):
    pend = PENDING.get(chat_id)
    if not pend or pend.get("id") != pending_id or pend.get("kind") != "waiting_to_select":
        return
    pend["kind"] = "participation"
    pend["responses"] = {}
    pend["timer"] = threading.Timer(SELECTION_TIMEOUT_SECONDS, expire_participation, args=(chat_id, pending_id))
    pend["timer"].daemon = True
    pend["timer"].start()
    send(
        chat_id,
        "*Were you part of this receipt?*\nChoose first; only people who answer *I was part of it* will see the item checklist.",
        keyboard=[[{"text": "I was part of it", "callback_data": "participation:yes"},
                   {"text": "I wasn't part of it", "callback_data": "participation:no"}]],
    )


def begin_item_selection(chat_id, pend):
    selected = {user_id: name for user_id, name in pend["all_participants"].items() if pend["responses"].get(user_id)}
    if not selected:
        del PENDING[chat_id]
        return send(chat_id, "No one confirmed participation, so no split was recorded.")
    pend["participants"] = selected
    pend["kind"] = "items"
    pend["claims"] = {}
    pend["responded"] = set()
    # Rebase the proposed total among confirmed participants before assigning
    # their individual selections.
    equal_share = pend["receipt"]["total_cents"] // len(selected)
    pend["prop"]["split"] = negotiator._force_sum(
        {name: equal_share for name in selected.values()}, pend["receipt"]["total_cents"], pend["payer"]
    )
    pend["timer"] = threading.Timer(SELECTION_TIMEOUT_SECONDS, expire_item_selection, args=(chat_id, pend["id"]))
    pend["timer"].daemon = True
    pend["timer"].start()
    sent = send(chat_id, selection_text(pend), keyboard=selection_keyboard(pend))
    pend["message_id"] = sent.get("result", {}).get("message_id")


def expire_participation(chat_id, pending_id):
    pend = PENDING.get(chat_id)
    if not pend or pend.get("kind") != "participation" or pend.get("id") != pending_id:
        return
    begin_item_selection(chat_id, pend)


# ---------------------------------------------------------------- handlers

def handle_photo(chat_id, msg):
    payer = name_of(msg["from"])
    typing(chat_id)
    send(chat_id, "_Reading the receipt..._")

    receipt = parser.parse(download_photo(msg["photo"]))
    if "_error" in receipt:
        return send(chat_id, f"Couldn't read that receipt: {receipt['_error']}")

    lines = "\n".join(
        f"  • {i['name']} — {ledger.money(i['cents'])}" for i in receipt.get("items", [])[:12]
    )
    warn = "\n⚠️ items don't sum to the printed total — check me" if receipt.get("total_mismatch") else ""
    send(
        chat_id,
        f"*{receipt.get('merchant', 'Receipt')}* — {ledger.money(receipt['total_cents'])}\n{lines}{warn}",
    )

    names = [m["name"] for m in ledger.members(chat_id)] or [payer]
    typing(chat_id)
    prop = negotiator.propose(receipt, names, payer)
    if "_error" in prop:
        return send(chat_id, f"Split agent failed: {prop['_error']}")

    selectable = negotiator.selectable_items(receipt)
    if len(names) > 1 and selectable:
        participant_names = {member["user_id"]: member["name"] for member in ledger.members(chat_id)}
        delay = FINALIZE_DELAYS.get(chat_id, 0)
        PENDING[chat_id] = {
            "kind": "waiting_to_select",
            "receipt": receipt,
            "prop": prop,
            "payer": payer,
            "items": [{"name": name, "cents": cents} for _, cents, name in selectable],
            "all_participants": participant_names,
            "finalize_note": finalize_time_text(delay),
        }
        pend = PENDING[chat_id]
        pend["id"] = time.monotonic_ns()
        pend["timer"] = threading.Timer(delay, begin_participation, args=(chat_id, pend["id"]))
        pend["timer"].daemon = True
        pend["timer"].start()
        if delay:
            return send(chat_id, f"Receipt saved. {finalize_time_text(delay)} I will open the group check-in then.")
        return begin_participation(chat_id, pend["id"])

    commit_split(chat_id, receipt, prop["split"], payer, prop.get("assumptions", []))


def commit_split(chat_id, receipt, split, payer, assumptions):
    eid = ledger.append(
        chat_id,
        "expense",
        {
            "merchant": receipt.get("merchant", "expense"),
            "total_cents": receipt["total_cents"],
            "payer": payer,
            "shares": split,
            "items": receipt.get("items", []),
            "assumptions": assumptions,
        },
    )
    body = "\n".join(f"  {n} — {ledger.money(c)}" for n, c in sorted(split.items()))
    why = "\n_" + "; ".join(assumptions[:2]) + "_" if assumptions else ""
    send(
        chat_id,
        f"*Fair split* (#{eid}) — {payer} paid\n{body}{why}\n\n"
        f"Disagree? Reply to this with what's wrong, or /settle when you're done.",
    )


def item_split(pend):
    """Start with the model proposal, then charge claimed items to claimers."""
    split = dict(pend["prop"]["split"])
    members = list(pend["participants"].values())
    for name in members:
        split.setdefault(name, 0)
    for index, claimers in pend["claims"].items():
        if not claimers:
            continue
        cents = pend["items"][index]["cents"]
        # Remove an equal allocation from everyone, then assign this item only
        # to the people who explicitly selected it. Rounding stays deterministic.
        per_member, member_remainder = divmod(cents, len(members))
        for position, name in enumerate(members):
            split[name] -= per_member + (1 if position < member_remainder else 0)
        claimers = sorted(claimers)
        per_claimer, claimer_remainder = divmod(cents, len(claimers))
        for position, name in enumerate(claimers):
            split[name] = split.get(name, 0) + per_claimer + (1 if position < claimer_remainder else 0)
    return negotiator._force_sum(split, pend["receipt"]["total_cents"], pend["payer"])


def finalize_item_selection(chat_id, pending_id, expired=False):
    pend = PENDING.get(chat_id)
    if not pend or pend.get("kind") != "items" or pend.get("id") != pending_id:
        return
    expected = set(pend["participants"])
    if not expired and pend["responded"] != expected:
        return
    if not expired and pend["finalize_delay"]:
        # The whole group answered early, but they explicitly chose a later
        # finalization time. The existing timer will commit at that deadline.
        pend["finalize_note"] = "scheduled after the requested wait period"
        refresh_selection_message(chat_id, pend)
        return
    pend["timer"].cancel()
    del PENDING[chat_id]
    missing = expected - pend["responded"]
    assumptions = ["Item claims confirmed by all group members"]
    if missing:
        missing_names = ", ".join(pend["participants"][member] for member in sorted(missing))
        assumptions = [f"Selection window expired; used the proposed split for {missing_names}"]
    assumptions.append(item_selection_reasoning(pend, missing))
    commit_split(chat_id, pend["receipt"], item_split(pend), pend["payer"], assumptions)


def expire_item_selection(chat_id, pending_id):
    finalize_item_selection(chat_id, pending_id, expired=True)


def item_selection_reasoning(pend, missing):
    selections = []
    for name in pend["participants"].values():
        chosen = [pend["items"][index]["name"] for index, claimers in pend["claims"].items() if name in claimers]
        if chosen:
            selections.append(f"{name}: {', '.join(chosen)}")
    shared = [item.get("name", "shared charges") for item in pend["receipt"].get("items", []) if item.get("shared")]
    reason = "Individual items were charged only to the people who selected them"
    if selections:
        reason += " (" + "; ".join(selections) + ")"
    if shared:
        reason += "; shared charges were divided across the group"
    if missing:
        reason += "; members without a response kept the proposed allocation"
    return reason + "."


def apply_mediation(chat_id, pend):
    exp, out = pend["expense"], pend["proposal"]
    old_split, new_split = exp["shares"], out["new_split"]
    ledger.append(
        chat_id,
        "adjustment",
        {
            "merchant": exp["merchant"] + " (mediated)",
            "total_cents": 0,
            "payer": exp["payer"],
            "shares": {
                name: new_split.get(name, 0) - old_split.get(name, 0)
                for name in set(old_split) | set(new_split)
            },
            "reason": out.get("reasoning", ""),
        },
    )
    del PENDING[chat_id]
    send(chat_id, "*Mediator proposal applied.* The original split remains in the audit log.")


def handle_callback(cb):
    chat_id = cb["message"]["chat"]["id"]
    who = name_of(cb["from"])
    data = cb.get("data", "")
    if data.startswith("schedule:"):
        choices = {"now": (0, "immediately after responses"), "30m": (1800, "30 minutes after the receipt"),
                   "2h": (7200, "2 hours after the receipt"), "eod": (parse_finalize_delay("eod"), "end of day")}
        choice = data.split(":", 1)[1]
        if choice not in choices:
            return answer_callback(cb["id"], "Unknown schedule.")
        delay, label = choices[choice]
        answer_callback(cb["id"], "Finalization time saved.")
        pend = PENDING.get(chat_id)
        if pend and pend.get("kind") == "schedule_receipt":
            pend["kind"] = "waiting_to_select"
            pend["finalize_note"] = finalize_time_text(delay)
            pend["timer"] = threading.Timer(delay, begin_participation, args=(chat_id, pend["id"]))
            pend["timer"].daemon = True
            pend["timer"].start()
            if delay:
                return send(chat_id, f"{finalize_time_text(delay)} I will ask who was part of the receipt at that time.")
            return begin_participation(chat_id, pend["id"])
        return set_finalize_delay(chat_id, delay, label)
    pend = PENDING.get(chat_id)
    if not pend or ":" not in data:
        return answer_callback(cb["id"], "This action has expired.")

    scope, action = data.split(":", 1)
    if scope != pend["kind"]:
        return answer_callback(cb["id"], "This action has expired.")
    if action == "cancel" or (scope == "mediation" and action == "keep"):
        if scope == "items":
            pend["timer"].cancel()
        del PENDING[chat_id]
        answer_callback(cb["id"], "Kept the original split.")
        return send(chat_id, "*No changes made.* The original split remains in the ledger.")

    if scope == "participation":
        user_id = cb["from"]["id"]
        if user_id not in pend["all_participants"]:
            return answer_callback(cb["id"], "Only group members in this receipt can respond.")
        pend["responses"][user_id] = action == "yes"
        answer_callback(cb["id"], "Participation saved.")
        expected = set(pend["all_participants"])
        if set(pend["responses"]) == expected:
            pend["timer"].cancel()
            return begin_item_selection(chat_id, pend)
        return

    if scope == "items":
        user_id = cb["from"]["id"]
        if user_id not in pend["participants"]:
            return answer_callback(cb["id"], "Only members in this split can respond.")
        if action == "change":
            if user_id in pend["responded"]:
                pend["responded"].remove(user_id)
                refresh_selection_message(chat_id, pend)
                return answer_callback(cb["id"], "You can change your items now.")
            return answer_callback(cb["id"], "You're already editing your items.")
        if user_id in pend["responded"]:
            return answer_callback(cb["id"], "You already finished selecting.")
        if action.startswith("select:"):
            try:
                index = int(action.split(":", 1)[1])
                item = pend["items"][index]
            except (ValueError, IndexError):
                return answer_callback(cb["id"], "That item is no longer available.")
            claimers = pend["claims"].setdefault(index, set())
            if who in claimers:
                claimers.remove(who)
                answer_callback(cb["id"], f"Removed {item['name']}.")
            else:
                claimers.add(who)
                answer_callback(cb["id"], f"Added {item['name']}.")
            refresh_selection_message(chat_id, pend)
            return
        if action == "done":
            pend["responded"].add(user_id)
            remaining = len(set(pend["participants"]) - pend["responded"])
            answer_callback(cb["id"], "Thanks — your items are saved.")
            refresh_selection_message(chat_id, pend)
            if remaining:
                return send(chat_id, f"{who} selected: " + ", ".join(
                    pend["items"][index]["name"] for index, claimers in pend["claims"].items() if who in claimers
                ) + f". Waiting for {remaining} more response(s).")
            return finalize_item_selection(chat_id, pend["id"])

    if scope == "mediation" and action == "apply":
        answer_callback(cb["id"], "Applying mediator proposal.")
        return apply_mediation(chat_id, pend)

    answer_callback(cb["id"], "Unknown action.")


def handle_settle(chat_id):
    net = ledger.balances(chat_id)
    if not net:
        return send(chat_id, "Nothing outstanding — you're square.")
    transfers = settler.simplify(net)
    keep, dropped = settler.prune(transfers)
    typing(chat_id)
    verdict = settler.vibe(net, keep, dropped, len(ledger.events(chat_id, "expense")))

    if keep:
        body = "\n".join(f"  {d} → {c}  {ledger.money(a)}" for d, c, a in keep)
    else:
        body = "  _nothing worth moving_"
    extra = ""
    if dropped:
        extra = "\n\n_Ignoring: " + ", ".join(
            f"{d}→{c} {ledger.money(a)}" for d, c, a in dropped
        ) + "_"
    send(chat_id, f"*Settle up* ({len(transfers)} transfer(s) → {len(keep)})\n{body}{extra}\n\n{verdict}")


def handle_explain(chat_id):
    """Explain the latest bill using recorded facts, never reconstructed LLM math."""
    expenses = ledger.events(chat_id, "expense")
    if not expenses:
        return send(chat_id, "There isn't a recorded split to explain yet. Send a receipt first.")

    expense = expenses[-1]
    shares = expense.get("shares", {})
    total = int(expense.get("total_cents", sum(shares.values())))
    lines = [
        f"*How the {expense.get('merchant', 'latest')} split works*",
        f"{expense.get('payer', 'Someone')} paid {ledger.money(total)}.",
        "That receipt was allocated as:",
    ]
    lines.extend(f"  • {name}: {ledger.money(cents)}" for name, cents in sorted(shares.items()))

    assumptions = expense.get("assumptions") or []
    if assumptions:
        lines.append("\n*Why:* " + "; ".join(assumptions))

    items = expense.get("items") or []
    if items:
        listed = ", ".join(
            f"{item.get('name', 'item')} ({ledger.money(int(item.get('cents', 0)))})"
            for item in items[:8]
        )
        if len(items) > 8:
            listed += ", …"
        lines.append("\n*Receipt items:* " + listed)

    adjustments = [
        event for event in ledger.events(chat_id, "adjustment")
        if event.get("merchant") == expense.get("merchant", "expense") + " (mediated)"
    ]
    if adjustments:
        adjustment = adjustments[-1]
        changes = [
            f"{name} {'+' if int(delta) > 0 else ''}{ledger.money(int(delta))}"
            for name, delta in sorted(adjustment.get("shares", {}).items()) if int(delta)
        ]
        lines.append("\n*A mediator later adjusted it:* " + ", ".join(changes) + ".")
        if adjustment.get("reason"):
            lines.append(adjustment["reason"])

    allocated = sum(int(cents) for cents in shares.values())
    lines.append(
        f"\nCheck: the recorded shares total {ledger.money(allocated)}"
        + (", matching the receipt." if allocated == total else f", while the receipt total is {ledger.money(total)}.")
    )
    return send(chat_id, "\n".join(lines))


def handle_dispute(chat_id, msg, complaint):
    if PENDING.get(chat_id, {}).get("kind") == "items":
        return send(chat_id, "Finish or cancel the current item selection before starting a dispute.")
    expenses = ledger.events(chat_id, "expense")
    if not expenses:
        return send(chat_id, "No expense logged yet to dispute.")
    exp = expenses[-1]
    PENDING[chat_id] = {"kind": "dispute_input", "expense": exp, "complaint": complaint}
    return send(
        chat_id,
        "Before I mediate, one question: *which specific item is wrong, and what did you have instead?* "
        "Reply with the detail and I will propose a revised, explained split.",
        reply_to=msg["message_id"],
    )


def resolve_dispute_input(chat_id, msg, detail):
    pend = PENDING.get(chat_id)
    if not pend or pend.get("kind") != "dispute_input":
        return
    exp = pend["expense"]
    typing(chat_id)
    send(chat_id, "_Mediating..._")
    out = mediator.mediate(
        {"items": exp.get("items", [])},
        exp["shares"],
        transcript_text(chat_id),
        pend["complaint"] + "\nFollow-up detail: " + detail,
    )
    if "_error" in out:
        del PENDING[chat_id]
        return send(chat_id, f"Mediator failed: {out['_error']}")

    names = set(exp["shares"]) | set(out["new_split"])
    before_after = "\n".join(
        f"  {n}: {ledger.money(exp['shares'].get(n, 0))} → {ledger.money(out['new_split'].get(n, 0))}"
        for n in sorted(names)
    )
    PENDING[chat_id] = {"kind": "mediation", "expense": exp, "proposal": out}
    send(
        chat_id,
        f"*Mediator proposal* — _{out.get('contested_item', 'contested item')}_\n"
        f"{before_after}\n\n{out.get('reasoning', '')}\n"
        f"_confidence: {out.get('confidence', '?')}_\n\n"
        "Review it together, then choose whether to update the ledger.",
        keyboard=[
            [{"text": "Apply mediator proposal", "callback_data": "mediation:apply"},
             {"text": "Keep original split", "callback_data": "mediation:keep"}],
        ],
        reply_to=msg["message_id"],
    )


def handle_nudge(chat_id, delay=0):
    net = ledger.balances(chat_id)
    transfers, _ = settler.prune(settler.simplify(net))
    if not transfers:
        return send(chat_id, "Nobody owes anybody enough to chase.")
    d, c, a = transfers[0]
    expenses = ledger.events(chat_id, "expense")
    what = expenses[-1]["merchant"] if expenses else "the bill"

    def fire():
        send(chat_id, nudge.draft(d, c, a, what, transcript_text(chat_id)))

    if delay:
        nudge.schedule(delay, fire)
        send(chat_id, f"_I'll follow up in {delay}s if it's still unpaid._")
    else:
        typing(chat_id)
        fire()


HELP = (
    "*FairShare* — I split bills where you actually argue about them.\n\n"
    "`/fair <what you need>` — ask naturally: settle up, remind someone, set a deadline, or dispute an item.\n"
    "📸 *Send a receipt photo* — I itemise it and propose a _fair_ split, not an equal one.\n"
    "↩️ *Reply to a split* with what's wrong — a mediator proposes a change for the group to approve.\n"
    "`/settle` — minimum transfers, plus whether it's even worth it.\n"
    "`/explain` — show the ledger-backed reasoning for the latest split.\n"
    "`/nudge` — I write the awkward reminder for you.\n"
    "`/nudge 20` — and I'll send it on my own in 20s.\n"
    "`/finalize 2h` — wait two hours before finalizing future receipt splits (`30m`, `eod`, or `now` also work).\n"
)

DISPUTE_RE = re.compile(
    r"didn'?t (order|have|eat|drink)|not mine|wasn'?t me|i don'?t drink|that'?s wrong|unfair",
    re.I,
)

FAIR_INTENT_SYSTEM = """Classify a FairShare group-expense request.

Choose exactly one intent:
- settle: show who should pay whom or settle up
- nudge: write or schedule a payment reminder
- finalize: set how long future receipt item-selection windows should wait
- dispute: challenge a receipt split or say an item was not theirs
- explain: explain how the latest receipt split or current balances were calculated
- help: ask what FairShare can do, or anything unrelated/unclear

Extract `delay` only for nudge as a non-negative integer number of seconds.
Extract `finalize` only as one of: now, <positive integer>m, <positive integer>h, eod.
For a dispute, put the user's original complaint in `detail`.
Never invent payment amounts, names, receipt items, or delays. Return only JSON."""

FAIR_INTENT_HINT = """Schema:
{"intent":"settle"|"nudge"|"finalize"|"dispute"|"explain"|"help",
 "delay":null|int, "finalize":null|str, "detail":null|str}"""


def fallback_fair_intent(request):
    """Useful offline fallback when the intent model is unavailable."""
    text = request.lower().strip()
    if any(word in text for word in ("explain", "logic", "breakdown", "how was", "how did", "why am i", "why do i")):
        return {"intent": "explain"}
    if DISPUTE_RE.search(text):
        return {"intent": "dispute", "detail": request}
    if any(word in text for word in ("settle", "owe", "owed", "who pays", "who should pay")):
        return {"intent": "settle"}
    if any(word in text for word in ("remind", "nudge", "chase", "follow up")):
        seconds = re.search(r"\b(\d+)\s*(?:seconds?|secs?|s)\b", text)
        return {"intent": "nudge", "delay": int(seconds.group(1)) if seconds else 0}
    if any(word in text for word in ("finalize", "wait", "deadline", "end of day", "eod")):
        value = re.search(r"\b(\d+\s*[mh])\b", text)
        if "eod" in text or "end of day" in text:
            choice = "eod"
        elif value:
            choice = value.group(1).replace(" ", "")
        elif "now" in text:
            choice = "now"
        else:
            choice = None
        return {"intent": "finalize", "finalize": choice}
    return {"intent": "help"}


def fair_intent(request):
    """Return a validated intent; model failure must not break the chat command."""
    fallback = fallback_fair_intent(request)
    out = json_call(FAIR_INTENT_SYSTEM, request, schema_hint=FAIR_INTENT_HINT)
    if "_error" in out or out.get("intent") not in {"settle", "nudge", "finalize", "dispute", "explain", "help"}:
        return fallback
    intent = out["intent"]
    result = {"intent": intent}
    if intent == "nudge":
        try:
            result["delay"] = max(0, int(out.get("delay") or 0))
        except (TypeError, ValueError):
            result["delay"] = fallback.get("delay", 0)
    elif intent == "finalize":
        value = str(out.get("finalize") or "").strip().lower()
        result["finalize"] = value if parse_finalize_delay(value) is not None else fallback.get("finalize")
    elif intent == "dispute":
        result["detail"] = str(out.get("detail") or request).strip()
    return result


def handle_fair(chat_id, msg, request):
    """Natural-language command gateway for the existing, deterministic handlers."""
    if not request.strip():
        return send(chat_id, "Try `/fair who owes what`, `/fair remind Sam in 20 seconds`, "
                    "`/fair wait 2h`, or `/fair I didn't have the wine`.")

    intent = fair_intent(request)
    if intent["intent"] == "settle":
        return handle_settle(chat_id)
    if intent["intent"] == "explain":
        return handle_explain(chat_id)
    if intent["intent"] == "nudge":
        return handle_nudge(chat_id, intent.get("delay", 0))
    if intent["intent"] == "finalize":
        value = intent.get("finalize")
        delay = parse_finalize_delay(value or "")
        if delay is None:
            return send(chat_id, "Tell me a wait time, such as `/fair wait 30m`, `/fair wait 2h`, or `/fair finalize eod`.")
        FINALIZE_DELAYS[chat_id] = delay
        if delay:
            return send(chat_id, f"Future receipt splits will post after the selected {value} wait period, even if everyone finishes early.")
        return send(chat_id, "Future receipt splits will post as soon as everyone finishes, or when the selection window expires.")
    if intent["intent"] == "dispute":
        return handle_dispute(chat_id, msg, intent.get("detail", request))
    return send(chat_id, "I can explain a split, settle up, nudge someone, set a receipt deadline, or mediate a disputed item. "
                "For example: `/fair who owes what`.")


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    if "from" in msg:
        ledger.remember_member(chat_id, msg["from"]["id"], name_of(msg["from"]))
    text = msg.get("text", "") or msg.get("caption", "") or ""
    if text:
        TRANSCRIPT[chat_id].append(f"{name_of(msg['from'])}: {text}")

    cmd = text.split()[0].split("@")[0].lower() if text else ""
    pending = PENDING.get(chat_id)
    if pending and pending.get("kind") == "dispute_input" and text and not cmd:
        return resolve_dispute_input(chat_id, msg, text)
    if "photo" in msg:
        return handle_photo(chat_id, msg)
    if cmd in ("/start", "/help"):
        send(chat_id, HELP)
        return send(chat_id, "*When should I publish future receipt splits?*", keyboard=finalize_keyboard())
    if cmd == "/fair":
        return handle_fair(chat_id, msg, text[len("/fair"):].strip())
    if cmd == "/settle":
        return handle_settle(chat_id)
    if cmd == "/explain":
        return handle_explain(chat_id)
    if cmd == "/nudge":
        parts = text.split()
        return handle_nudge(chat_id, int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
    if cmd == "/finalize":
        value = text[len("/finalize"):].strip()
        if not value:
            return send(
                chat_id,
                "*When should I publish the final split for future receipts?*",
                keyboard=[
                    [{"text": "When everyone is done", "callback_data": "schedule:now"}],
                    [{"text": "In 30 minutes", "callback_data": "schedule:30m"},
                     {"text": "In 2 hours", "callback_data": "schedule:2h"}],
                    [{"text": "End of day", "callback_data": "schedule:eod"}],
                ],
            )
        delay = parse_finalize_delay(value)
        if delay is None:
            return send(chat_id, "Use `/finalize now`, `/finalize 30m`, `/finalize 2h`, or `/finalize eod`.")
        return set_finalize_delay(chat_id, delay, value)
    if cmd == "/dispute":
        return handle_dispute(chat_id, msg, text[len("/dispute"):].strip() or "unspecified")
    if DISPUTE_RE.search(text) and ledger.events(chat_id, "expense"):
        return handle_dispute(chat_id, msg, text)


# ------------------------------------------------------------------- loop

def main():
    ledger.init()
    if not TOKEN or TOKEN.startswith("your-"):
        raise SystemExit("TELEGRAM_BOT_TOKEN is missing or still a placeholder in .env.")
    try:
        response = requests.get(f"{API}/getMe", timeout=20)
        response.raise_for_status()
        me = response.json()
    except (requests.RequestException, ValueError) as exc:
        raise SystemExit(f"Could not reach Telegram's getMe endpoint: {exc}") from exc
    if not me.get("ok") or not me.get("result"):
        description = me.get("description", "Telegram returned no bot details.")
        code = me.get("error_code", "unknown")
        raise SystemExit(
            f"Telegram getMe failed ({code}): {description}. "
            "Check TELEGRAM_BOT_TOKEN in .env and generate a new token in @BotFather if needed."
        )
    print("FairShare online as @%s" % me["result"]["username"])
    offset = None
    while True:
        try:
            r = requests.get(
                f"{API}/getUpdates",
                params={"timeout": 30, "offset": offset, "allowed_updates": '["message","callback_query"]'},
                timeout=45,
            ).json()
            for upd in r.get("result", []):
                offset = upd["update_id"] + 1
                try:
                    if "callback_query" in upd:
                        handle_callback(upd["callback_query"])
                    elif "message" in upd:
                        handle_message(upd["message"])
                except Exception as exc:  # keep the bot alive through any agent failure
                    print("handler error:", repr(exc))
        except Exception as exc:  # noqa: BLE001
            print("poll error:", repr(exc))
            time.sleep(2)


if __name__ == "__main__":
    main()
