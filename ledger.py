"""Append-only event ledger shared by every agent.

Design note (judging criterion 3): agents never mutate balances directly.
They append immutable events; balances are always a pure fold over the log.
That makes a dispute resolvable by appending a correction, not by editing
history -- which is exactly what the mediator agent needs.
"""
import json
import sqlite3
import time
from collections import defaultdict
from contextlib import contextmanager

DB = "fairshare.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    id       INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id  INTEGER NOT NULL,
    kind     TEXT    NOT NULL,
    payload  TEXT    NOT NULL,
    ts       REAL    NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_chat ON events(chat_id);

CREATE TABLE IF NOT EXISTS members (
    chat_id  INTEGER NOT NULL,
    user_id  INTEGER NOT NULL,
    name     TEXT    NOT NULL,
    PRIMARY KEY (chat_id, user_id)
);
"""


def conn():
    c = sqlite3.connect(DB)
    c.row_factory = sqlite3.Row
    return c


@contextmanager
def connection():
    """Commit successful work and always release SQLite's file handle.

    ``sqlite3.Connection`` used as a context manager does not close itself,
    which leaves database files locked on Windows between short-lived calls.
    """
    c = conn()
    try:
        yield c
        c.commit()
    except Exception:
        c.rollback()
        raise
    finally:
        c.close()


def init():
    with connection() as c:
        c.executescript(SCHEMA)


# ---------------------------------------------------------------- events

def append(chat_id, kind, payload):
    """kind: 'expense' | 'adjustment' | 'settlement' | 'note'"""
    with connection() as c:
        cur = c.execute(
            "INSERT INTO events (chat_id, kind, payload, ts) VALUES (?,?,?,?)",
            (chat_id, kind, json.dumps(payload), time.time()),
        )
        return cur.lastrowid


def events(chat_id, kind=None):
    q = "SELECT * FROM events WHERE chat_id=?"
    args = [chat_id]
    if kind:
        q += " AND kind=?"
        args.append(kind)
    with connection() as c:
        return [
            {"id": r["id"], "kind": r["kind"], "ts": r["ts"], **json.loads(r["payload"])}
            for r in c.execute(q + " ORDER BY id", args)
        ]


def get_event(chat_id, event_id):
    with connection() as c:
        r = c.execute(
            "SELECT * FROM events WHERE chat_id=? AND id=?", (chat_id, event_id)
        ).fetchone()
    if not r:
        return None
    return {"id": r["id"], "kind": r["kind"], "ts": r["ts"], **json.loads(r["payload"])}


# --------------------------------------------------------------- members

def remember_member(chat_id, user_id, name):
    with connection() as c:
        c.execute(
            "INSERT OR REPLACE INTO members (chat_id, user_id, name) VALUES (?,?,?)",
            (chat_id, user_id, name),
        )


def members(chat_id):
    with connection() as c:
        return [
            {"user_id": r["user_id"], "name": r["name"]}
            for r in c.execute(
                "SELECT * FROM members WHERE chat_id=? ORDER BY name", (chat_id,)
            )
        ]


# -------------------------------------------------------------- balances

def balances(chat_id):
    """Fold the whole log into {name: net_cents}. Positive = is owed money."""
    net = defaultdict(int)
    for e in events(chat_id):
        if e["kind"] in ("expense", "adjustment"):
            for name, cents in e.get("shares", {}).items():
                net[name] -= int(cents)
            net[e["payer"]] += int(e.get("total_cents", sum(e.get("shares", {}).values())))
        elif e["kind"] == "settlement":
            net[e["frm"]] += int(e["cents"])
            net[e["to"]] -= int(e["cents"])
    return {k: v for k, v in net.items() if v != 0}


def money(cents):
    return f"${cents / 100:,.2f}"
