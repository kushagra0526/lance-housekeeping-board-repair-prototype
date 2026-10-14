"""Simulated housekeeping days, five ways to run the board.

    python evaluate.py dev        # 30 days, DEV_CFG (used while building)
    python evaluate.py heldout    # 40 days, eval/heldout.py (frozen in git before the first run)

Morning board / during the day:
  generic  / static   rooms in number order cut into equal-credit sections, roster order; manual call-out handling
  sections / static   sections handed out in the learned order (who usually works where); no day changes
  affinity / free     generic optimizer: floor-wing affinity + local search, rebuilt from scratch on every event
  sections / edge     ablation: move single rooms across section edges when someone runs late
  sections / recut    re-cut the learned sections over the rooms left, sized by each person's time left, on every event
  sections / lazy     same re-cut, but only after a call-out or when someone is projected to run past the shift  <- the method
"""
import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from hkboard.plan import agreement  # noqa: E402
from hkboard.sim import Sim  # noqa: E402
from hkboard.world import DEV_CFG, make_case  # noqa: E402

VARIANTS = [("generic", "static"), ("sections", "static"), ("affinity", "free"), ("sections", "edge"), ("sections", "recut"), ("sections", "lazy")]
KEYS = ("overtime_min", "deps_late_3pm", "undone", "early_waited_15", "early_wait_min", "guests_waited", "guest_wait_min",
        "floor_changes", "moves", "rooms_moved", "rooms", "early_requests")


def run(split):
    if split == "heldout":
        from eval.heldout import CFG, SEEDS
    else:
        CFG, SEEDS = DEV_CFG, list(range(1, 31))
    rows = []
    for seed in SEEDS:
        c = make_case(seed, CFG)
        callout = any(e["type"] == "callout" for e in c["day"]["events"])
        row = {"seed": seed, "rooms": len(c["day"]["tasks"]), "attendants": len(c["on_shift"]), "callout": callout}
        for m, d in VARIANTS:
            t0 = time.perf_counter()
            s = Sim(c, m, d, iters=3000)
            r = s.run()
            r["agree"] = agreement(s.morning_board, c["supervisor"])
            r["sec"] = time.perf_counter() - t0
            row[f"{m}/{d}"] = r
        rows.append(row)
    summ = {}
    for m, d in VARIANTS:
        name = f"{m}/{d}"
        tot = {k: sum(r[name][k] for r in rows) for k in KEYS}
        tot["agree_pct"] = round(100 * sum(r[name]["agree"] for r in rows) / len(rows), 1)
        tot["overtime_per_day"] = round(tot["overtime_min"] / len(rows), 1)
        tot["callout_days_overtime"] = sum(r[name]["overtime_min"] for r in rows if r["callout"])
        tot["sec_per_day"] = round(sum(r[name]["sec"] for r in rows) / len(rows), 2)
        summ[name] = tot
    ref = "sections/lazy"
    wins = {name: sum(r[ref]["overtime_min"] <= r[name]["overtime_min"] for r in rows) for name in summ if name != ref}
    return {"split": split, "days": len(rows), "callout_days": sum(r["callout"] for r in rows), "summary": summ,
            "recut_overtime_le": wins, "rows": rows}


def show(r):
    print(f"\n== {r['split']}: {r['days']} days ({r['callout_days']} with a call-out)")
    cols = ("agree_pct", "overtime_min", "overtime_per_day", "callout_days_overtime", "deps_late_3pm", "undone",
            "early_waited_15", "guests_waited", "floor_changes", "moves", "rooms_moved", "sec_per_day")
    print(f"{'board / day':<18}" + "".join(f"{c[:14]:>15}" for c in cols))
    for name, v in r["summary"].items():
        print(f"{name:<18}" + "".join(f"{v[c]:>15}" for c in cols))
    print("days where lazy re-cut overtime <= other:", r["recut_overtime_le"])


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    split = sys.argv[1] if len(sys.argv) > 1 else "dev"
    r = run(split)
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / f"out/eval_{split}.json").write_text(json.dumps(r, indent=1), encoding="utf-8")
    show(r)
