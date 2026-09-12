"""FairShare -- a bill-splitting agent swarm that lives in the group chat.

Deliberately uses the raw Telegram Bot API over `requests` with long polling:
no webhook, no ngrok, no public URL, nothing to break during a live demo.

Run:  python bot.py
"""
import os
import re
import time
from collections import defaultdict, deque

import requests
from dotenv import load_dotenv

import ledger
from agents import mediator, negotiator, nudge, parser, settler

load_dotenv()
TOKEN = os.environ["TELEGRAM_BOT_TOKEN"]
API = f"https://api.telegram.org/bot{TOKEN}"
FILE_API = f"https://api.telegram.org/file/bot{TOKEN}"

# Rolling conversation context per chat -- this is the raw material the
# mediator and nudge agents reason over. The environment IS the data source.
TRANSCRIPT = defaultdict(lambda: deque(maxlen=40))
# chat_id -> pending receipt awaiting a group answer
PENDING = {}


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


def name_of(user):
    return user.get("first_name") or user.get("username") or str(user["id"])


def transcript_text(chat_id):
    return "\n".join(TRANSCRIPT[chat_id]) or "(no conversation yet)"


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

    q = prop.get("question")
    if q:
        PENDING[chat_id] = {"receipt": receipt, "prop": prop, "payer": payer, "claims": []}
        kb = [[{"text": n, "callback_data": f"had:{n}"} for n in names[:4]]]
        kb.append([{"text": "everyone did", "callback_data": "had:*"}])
        return send(chat_id, f"❓ {q['text']}\n_Tap if that was you._", keyboard=kb)

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
        },
    )
    body = "\n".join(f"  {n} — {ledger.money(c)}" for n, c in sorted(split.items()))
    why = "\n_" + "; ".join(assumptions[:2]) + "_" if assumptions else ""
    send(
        chat_id,
        f"*Fair split* (#{eid}) — {payer} paid\n{body}{why}\n\n"
        f"Disagree? Reply to this with what's wrong, or /settle when you're done.",
    )


def handle_callback(cb):
    chat_id = cb["message"]["chat"]["id"]
    who = name_of(cb["from"])
    pend = PENDING.get(chat_id)
    answer_callback(cb["id"], "got it")
    if not pend:
        return
    choice = cb["data"].split(":", 1)[1]
    pend["claims"].append(who if choice != "*" else "everyone")

    q = pend["prop"]["question"]
    claimers = pend["claims"]
    split = dict(pend["prop"]["split"])
    if "everyone" not in claimers and claimers:
        # Reassign the contested item to whoever owned up, evenly.
        cents = int(q["cents"])
        per = cents // len(claimers)
        rem = cents - per * len(claimers)
        for n in split:
            split[n] = max(0, split[n] - cents // max(1, len(split)))
        for idx, n in enumerate(claimers):
            split[n] = split.get(n, 0) + per + (rem if idx == 0 else 0)
        split = negotiator._force_sum(split, pend["receipt"]["total_cents"], pend["payer"])

    del PENDING[chat_id]
    commit_split(
        chat_id,
        pend["receipt"],
        split,
        pend["payer"],
        [f"{q['item']} assigned to {', '.join(claimers)}"],
    )


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


def handle_dispute(chat_id, msg, complaint):
    expenses = ledger.events(chat_id, "expense")
    if not expenses:
        return send(chat_id, "No expense logged yet to dispute.")
    exp = expenses[-1]
    typing(chat_id)
    send(chat_id, "_Mediating..._")
    out = mediator.mediate(
        {"items": exp.get("items", [])},
        exp["shares"],
        transcript_text(chat_id),
        complaint,
    )
    if "_error" in out:
        return send(chat_id, f"Mediator failed: {out['_error']}")

    ledger.append(
        chat_id,
        "adjustment",
        {
            "merchant": exp["merchant"] + " (mediated)",
            "total_cents": 0,
            "payer": exp["payer"],
            "shares": {k: out["new_split"].get(k, 0) - v for k, v in exp["shares"].items()},
            "reason": out.get("reasoning", ""),
        },
    )
    before_after = "\n".join(
        f"  {n}: {ledger.money(exp['shares'].get(n, 0))} → {ledger.money(c)}"
        for n, c in sorted(out["new_split"].items())
    )
    send(
        chat_id,
        f"*Mediator's call* — _{out.get('contested_item', 'contested item')}_\n"
        f"{before_after}\n\n{out.get('reasoning', '')}\n"
        f"_confidence: {out.get('confidence', '?')}_",
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
    "📸 *Send a receipt photo* — I itemise it and propose a _fair_ split, not an equal one.\n"
    "↩️ *Reply to a split* with what's wrong — a mediator agent arbitrates.\n"
    "`/settle` — minimum transfers, plus whether it's even worth it.\n"
    "`/nudge` — I write the awkward reminder for you.\n"
    "`/nudge 20` — and I'll send it on my own in 20s.\n"
)

DISPUTE_RE = re.compile(
    r"didn'?t (order|have|eat|drink)|not mine|wasn'?t me|i don'?t drink|that'?s wrong|unfair",
    re.I,
)


def handle_message(msg):
    chat_id = msg["chat"]["id"]
    if "from" in msg:
        ledger.remember_member(chat_id, msg["from"]["id"], name_of(msg["from"]))
    text = msg.get("text", "") or msg.get("caption", "") or ""
    if text:
        TRANSCRIPT[chat_id].append(f"{name_of(msg['from'])}: {text}")

    cmd = text.split()[0].split("@")[0].lower() if text else ""
    if "photo" in msg:
        return handle_photo(chat_id, msg)
    if cmd in ("/start", "/help"):
        return send(chat_id, HELP)
    if cmd == "/settle":
        return handle_settle(chat_id)
    if cmd == "/nudge":
        parts = text.split()
        return handle_nudge(chat_id, int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 0)
    if cmd == "/dispute":
        return handle_dispute(chat_id, msg, text[len("/dispute"):].strip() or "unspecified")
    if DISPUTE_RE.search(text) and ledger.events(chat_id, "expense"):
        return handle_dispute(chat_id, msg, text)


# ------------------------------------------------------------------- loop

def main():
    ledger.init()
    me = requests.get(f"{API}/getMe", timeout=20).json()
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
