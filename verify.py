"""Money-math verification. Run: python verify.py (no API key needed)."""
from agents.settler import prune, simplify
import ledger, os

def test_simplify_nets_out():
    net = {"Palak": 6000, "Sam": -2500, "Priya": -1500, "Wei": -2000}
    t = simplify(net)
    assert sum(a for _, _, a in t) == 6000
    assert len(t) <= len(net) - 1
    # every debtor's total outflow equals their debt
    for name, bal in net.items():
        if bal < 0:
            assert sum(a for d, _, a in t if d == name) == -bal

def test_three_way_circular_collapses():
    # A owes B, B owes C, C owes A -> should collapse, not 3 transfers
    net = {"A": -1000, "B": 0, "C": 1000}
    assert simplify(net) == [("A", "C", 1000)]

def test_prune_drops_trivial():
    keep, dropped = prune([("A", "B", 300), ("C", "D", 5000)])
    assert keep == [("C", "D", 5000)] and dropped == [("A", "B", 300)]

def test_ledger_fold_and_adjustment():
    if os.path.exists("test.db"): os.remove("test.db")
    ledger.DB = "test.db"; ledger.init()
    ledger.append(1, "expense", {"payer": "Palak", "total_cents": 9000,
                                 "shares": {"Palak": 3000, "Sam": 3000, "Priya": 3000}})
    assert ledger.balances(1) == {"Palak": 6000, "Sam": -3000, "Priya": -3000}
    # mediator shifts $10 off Priya onto Sam -- a delta event, history intact
    ledger.append(1, "adjustment", {"payer": "Palak", "total_cents": 0,
                                    "shares": {"Sam": 1000, "Priya": -1000}})
    b = ledger.balances(1)
    assert b["Sam"] == -4000 and b["Priya"] == -2000 and b["Palak"] == 6000
    assert sum(b.values()) == 0, "ledger must always balance to zero"
    ledger.append(1, "settlement", {"frm": "Sam", "to": "Palak", "cents": 4000})
    assert "Sam" not in ledger.balances(1)
    os.remove("test.db")


if __name__ == "__main__":
    import sys
    fns = [v for k, v in sorted(globals().items()) if k.startswith("test_")]
    for fn in fns:
        fn()
        print("PASS ", fn.__name__)
    print("\n%d/%d money-math checks passed." % (len(fns), len(fns)))
