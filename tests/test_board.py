import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from hkboard.plan import agreement, generic_board, learn_order, section_board  # noqa: E402
from hkboard.sim import Sim, prio  # noqa: E402
from hkboard.world import OPS, credits, make_case  # noqa: E402

CASE = make_case(17)


def test_learned_order_recovers_who_works_where():
    order = learn_order(CASE["history"], len(CASE["staff"]))
    on = CASE["on_shift"]
    by_learned = sorted(on, key=lambda a: order[a])
    by_home = sorted(on, key=lambda a: (CASE["staff"][a]["home"], CASE["staff"][a]["wing"], a))
    assert sum(x == y for x, y in zip(by_learned, by_home)) >= len(on) - 2


def test_sections_are_balanced_and_compact():
    d = CASE["day"]
    b = section_board(d["tasks"], d["rooms"], CASE["on_shift"], learn_order(CASE["history"], len(CASE["staff"])))
    loads = [sum(credits(d["tasks"][k], d["rooms"][k]) for k in ks) for ks in b.as_dict().values()]
    assert max(loads) - min(loads) <= 2.0
    for ks in b.as_dict().values():
        floors = sorted({d["rooms"][k]["floor"] for k in ks})
        assert floors[-1] - floors[0] <= 2
    gen = generic_board(d["tasks"], d["rooms"], CASE["on_shift"])
    assert agreement(b.as_dict(), CASE["supervisor"]) > agreement(gen.as_dict(), CASE["supervisor"]) + 0.5


def test_every_room_accounted_for_and_callout_respected():
    s = Sim(CASE, "sections", "lazy")
    s.run()
    gone = [a for a in s.on if s.att[a]["gone"]]
    callout_t = next(e["t"] for e in CASE["day"]["events"] if e["type"] == "callout")
    for k, v in s.tasks.items():
        assert v["status"] in ("done", "pending")
        if v["status"] == "done" and s.assign[k] in gone:
            assert v["done_at"] <= callout_t + 1
    assert all(s.assign[k] in s.on for k in s.tasks)


def test_planner_does_not_peek_at_checkout_times():
    s = Sim(CASE, "sections", "static")
    k = next(k for k, v in s.tasks.items() if v["kind"] == "dep" and v["out"] > 600)
    assert s.est_cleanable(k, 500) == 630          # "due out": the planner assumes 10:30
    s.known_until[k] = 840
    assert s.est_cleanable(k, 500) == 840          # after a late-checkout event it knows


def test_blocked_early_checkin_rooms_go_first():
    assert prio({"kind": "stay", "blocked_eta": 700}) < prio({"kind": "refresh"}) < prio({"kind": "dep"}) < prio({"kind": "stay"})
    from eval.heldout import CFG
    for seed in range(5001, 5041):
        s = Sim(make_case(seed, CFG), "sections", "lazy")
        s.run()
        blocked = [x for x in s.early if x["key"]]
        if blocked:
            break
    assert blocked and all(s.tasks[x["key"]]["status"] == "done" for x in blocked)


def test_lazy_recut_moves_less_than_recut_every_event():
    lazy = Sim(CASE, "sections", "lazy").run()
    every = Sim(CASE, "sections", "recut").run()
    assert lazy["moves"] <= every["moves"]
    assert lazy["overtime_min"] <= every["overtime_min"] + 15


def test_deterministic():
    a = Sim(CASE, "sections", "lazy").run()
    b = Sim(CASE, "sections", "lazy").run()
    assert a == b and OPS["shift_end"] == 990
