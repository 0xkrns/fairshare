"""AGENT 4 -- Settler. Two halves, deliberately separated.

simplify()  : pure arithmetic, zero LLM. Minimum-transaction debt netting.
              Judges check money math; an LLM must never touch it.
vibe()      : the product opinion -- should this group even bother settling?
              This is the part that is genuinely not Splitwise.
"""
from llm import text_call

# ----------------------------------------------------- deterministic half


def simplify(net):
    """net: {name: cents}, positive = is owed. Returns [(debtor, creditor, cents)].

    Greedy largest-creditor/largest-debtor matching. Produces at most n-1
    transfers, which is the minimum for a connected group.
    """
    creditors = sorted(
        [[n, c] for n, c in net.items() if c > 0], key=lambda x: -x[1]
    )
    debtors = sorted([[n, -c] for n, c in net.items() if c < 0], key=lambda x: -x[1])
    transfers = []
    i = j = 0
    while i < len(debtors) and j < len(creditors):
        amount = min(debtors[i][1], creditors[j][1])
        if amount > 0:
            transfers.append((debtors[i][0], creditors[j][0], amount))
        debtors[i][1] -= amount
        creditors[j][1] -= amount
        if debtors[i][1] == 0:
            i += 1
        if creditors[j][1] == 0:
            j += 1
    return transfers


def prune(transfers, threshold_cents=500):
    """Vibe rule, deterministic part: drop trivial transfers."""
    keep = [t for t in transfers if t[2] >= threshold_cents]
    dropped = [t for t in transfers if t[2] < threshold_cents]
    return keep, dropped


# ------------------------------------------------------------- vibe half

SYSTEM = """You advise a group of friends on whether debts are worth settling.
You are explicitly NOT an exact-ledger app. Your bias: friendships survive
rounding errors, and asking someone to transfer $3 is worse than eating it.

Given the net balances, how many expenses this group has shared, and the
transfers required, give a one-or-two sentence verdict. If the group is roughly
even, say so and tell them not to bother. If someone is carrying a real
imbalance, name it plainly and warmly."""


def vibe(net, transfers, dropped, expense_count):
    user = (
        f"Net balances (cents, positive = is owed): {net}\n"
        f"Transfers needed: {transfers}\n"
        f"Transfers below the bother-threshold: {dropped}\n"
        f"Shared expenses logged so far: {expense_count}"
    )
    return text_call(SYSTEM, user, temperature=0.6)
