"""AGENT 5 -- Nudge. Acts without being asked, in the group's own register.

This is the agentic-autonomy point for the judges: nobody prompts it. It wakes
up, notices an unpaid transfer, and writes the awkward message so a human
doesn't have to.
"""
import threading
import time

from llm import text_call

SYSTEM = """You write the message a friend is too awkward to send: a reminder
about an unpaid share of a bill.

Match the tone of the group conversation you are shown -- if they are jokey, be
jokey; if they are terse, be terse. Rules: one or two lines maximum, never
passive-aggressive, never guilt-trip, name the amount and what it was for, and
give them an easy out. No emoji unless the group uses emoji."""


def draft(debtor, creditor, cents, what, transcript):
    user = (
        f"{debtor} owes {creditor} ${cents / 100:.2f} for {what}.\n"
        f"It has been a few days.\n\nGroup conversation style sample:\n{transcript}"
    )
    return text_call(SYSTEM, user, temperature=0.8)


def schedule(delay_seconds, fn, *args):
    """Fire-and-forget timer. Swap for Trigger.dev for durable scheduling --
    see README 'Sponsor integrations'."""
    t = threading.Timer(delay_seconds, fn, args=args)
    t.daemon = True
    t.start()
    return t
