"""One simulated day (dev seed 17: 147 rooms, 9 attendants, a call-out at mid-morning) -> out/report.html

    python demo.py
"""
import html
import json
import sys
from pathlib import Path

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE))

from evaluate import VARIANTS  # noqa: E402
from hkboard.plan import agreement  # noqa: E402
from hkboard.sim import Sim  # noqa: E402
from hkboard.world import hm, make_case  # noqa: E402

SEED = 17
PAL = ["#4e79a7", "#f28e2b", "#59a14f", "#e15759", "#76b7b2", "#edc948", "#b07aa1", "#ff9da7", "#9c755f", "#bab0ac", "#86bcb6", "#d37295"]
e = html.escape
LABEL = {"generic/static": "generic split, static", "sections/static": "learned sections, static",
         "affinity/free": "optimizer rebuilt on every event", "sections/edge": "learned sections + edge moves",
         "sections/recut": "learned sections + re-cut every event", "sections/lazy": "learned sections + re-cut when needed"}


def build():
    case = make_case(SEED)
    sims = {}
    for m, d in VARIANTS:
        s = Sim(case, m, d, iters=3000)
        r = s.run()
        r["agree"] = agreement(s.morning_board, case["supervisor"])
        sims[f"{m}/{d}"] = (s, r)
    return case, sims


def grid(case, s, which, title):
    rooms = case["day"]["rooms"]
    assign = s.morning if which == "morning" else s.assign
    color = {}
    for k, a in assign.items():
        if isinstance(k, int):
            color[k] = a
    moved = s.moved if which != "morning" else set()
    gone = [a for a in s.on if s.att[a]["gone"]]
    floors = sorted({r["floor"] for r in rooms.values()}, reverse=True)
    rows = ""
    for f in floors:
        cells = ""
        for wing in "AB":
            for r in sorted((r for r in rooms.values() if r["floor"] == f and r["wing"] == wing), key=lambda r: r["idx"]):
                a = color.get(r["id"])
                bg = PAL[s.on.index(a) % len(PAL)] if a is not None else "#2a2f3a"
                cls = "c mv" if r["id"] in moved else "c"
                cells += f"<span class='{cls}' style='background:{bg}' title='{r['id']}'></span>"
            cells += "<span class='gap'></span>"
        rows += f"<div class='row'><span class='fl'>{f}</span>{cells}</div>"
    legend = "".join(f"<span class='lg'><span class='c' style='background:{PAL[i % len(PAL)]}'></span>{e(case['staff'][a]['name'])}"
                     f"{' (called out)' if a in gone and which != 'morning' else ''}</span>" for i, a in enumerate(s.on))
    sub = f"{len(s.moved)} rooms moved during the day (outlined)" if which != "morning" else "rooms coloured by attendant; grey = vacant clean"
    return f"<div class='card'><b>{e(title)}</b><div class='sub'>{sub}</div>{rows}<div class='legend'>{legend}</div></div>"


def main():
    sys.stdout.reconfigure(encoding="utf-8")
    case, sims = build()
    ev = case["day"]["events"]
    print(f"seed {SEED}: {len(case['day']['tasks'])} rooms to service, {len(case['on_shift'])} attendants, {len(ev)} events")
    for k, (s, r) in sims.items():
        print(f"  {LABEL[k]:<42} overtime {r['overtime_min']:>4} min  floor changes {r['floor_changes']:>3}  "
              f"rooms moved {r['rooms_moved']:>3}  late at 3pm {r['deps_late_3pm']}  agree {r['agree']:.0%}")
    evj = {}
    for k in ("dev", "heldout_first_run"):
        f = HERE / ("out/eval_dev.json" if k == "dev" else "out/heldout_first_run.json")
        if f.exists():
            evj[k] = json.loads(f.read_text(encoding="utf-8"))
    (HERE / "out").mkdir(exist_ok=True)
    (HERE / "out/report.html").write_text(render(case, sims, evj), encoding="utf-8")
    print("wrote out/report.html")


def render(case, sims, evj):
    s_lazy = sims["sections/lazy"][0]
    s_free = sims["affinity/free"][0]
    names = {a: case["staff"][a]["name"] for a in range(len(case["staff"]))}
    on = case["on_shift"]
    evs = ""
    for x in case["day"]["events"]:
        what = {"callout": lambda x: f"{names[on[x['who'] % len(on)]]} calls out",
                "early_checkin": lambda x: f"early check-in request, {x['rtype']}, ETA {hm(x['eta'])}",
                "late_checkout": lambda x: f"late checkout {x['room']} until {hm(x['until'])}",
                "early_departure": lambda x: f"{x['room']} leaves a day early",
                "dnd_off": lambda x: f"DND off {x['room']}", "refresh": lambda x: f"refresh request {x['room']}"}[x["type"]](x)
        evs += f"<li><span class='t'>{hm(x['t'])}</span> {e(what)}</li>"
    day_rows = "".join(f"<tr><td>{e(LABEL[k])}</td><td>{r['agree']:.0%}</td><td><b>{r['overtime_min']}</b></td><td>{r['deps_late_3pm']}</td>"
                       f"<td>{r['floor_changes']}</td><td>{r['moves']}</td><td>{r['rooms_moved']}</td></tr>" for k, (s, r) in sims.items())
    bench = ""
    for name, lab in (("dev", "dev, 30 days"), ("heldout_first_run", "held-out, 40 harder days (first run)")):
        if name not in evj:
            continue
        for k, v in evj[name]["summary"].items():
            bench += (f"<tr><td>{lab}</td><td>{e(LABEL[k])}</td><td>{v['agree_pct']}%</td><td><b>{v['overtime_per_day']}</b></td>"
                      f"<td>{v['undone']}</td><td>{v['floor_changes']}</td><td>{v['moves']}</td><td>{v['sec_per_day']}</td></tr>")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Housekeeping board repair</title><style>
:root{{--bg:#0f1218;--fg:#e9edf3;--mut:#93a0b4;--card:#171c25;--line:#2a3140}}
*{{box-sizing:border-box}}body{{margin:0;background:var(--bg);color:var(--fg);font:15px/1.5 system-ui,-apple-system,Segoe UI,sans-serif}}
main{{max-width:1180px;margin:0 auto;padding:26px 16px 60px}}h1{{font-size:25px;margin:0 0 4px}}h2{{font-size:17px;margin:26px 0 8px}}
.sub{{color:var(--mut);font-size:13.5px;margin-bottom:6px}}.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:14px}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:12px;padding:14px;overflow-x:auto}}
.row{{display:flex;align-items:center;gap:2px;margin:2px 0;white-space:nowrap}}.fl{{width:18px;color:var(--mut);font-size:12px}}
.c{{display:inline-block;width:10px;height:16px;border-radius:2px;flex:none}}.c.mv{{outline:2px solid #fff;outline-offset:-1px}}.gap{{width:8px;flex:none}}
.legend{{margin-top:8px;font-size:12.5px;color:var(--mut)}}.lg{{display:inline-flex;align-items:center;gap:4px;margin-right:10px}}
table{{width:100%;border-collapse:collapse;font-size:13.5px;background:var(--card)}}td,th{{border:1px solid var(--line);padding:5px 8px;text-align:left}}
ul{{margin:0;padding-left:18px;font-size:13.5px;columns:2}}.t{{color:var(--mut);font-family:Consolas,monospace}}
</style></head><body><main>
<h1>Housekeeping board: learn the supervisor's sections, re-cut only when the day needs it</h1>
<div class="sub">One simulated day: {len(case['day']['tasks'])} rooms to service on {len(case['hotel']) and len({r['floor'] for r in case['hotel']})} floors, {len(on)} attendants. Synthetic hotel and supervisor; operating numbers are assumptions.
Unofficial prototype by Kushagra Pratap Singh, not affiliated with Lance.</div>
<div class="grid">{grid(case, s_lazy, 'morning', 'Morning board, in the supervisor’s sections')}
{grid(case, s_lazy, 'end', 'End of day: re-cut when needed')}{grid(case, s_free, 'end', 'End of day: optimizer rebuilt on every event')}</div>
<h2>The day's events</h2><div class="card"><ul>{evs}</ul></div>
<h2>This day, six ways</h2><div style="overflow-x:auto"><table><tr><th>board</th><th>matches supervisor</th><th>overtime min</th><th>departures not clean at 15:00</th><th>floor changes</th><th>reassignments</th><th>rooms moved</th></tr>{day_rows}</table></div>
<h2>Benchmark</h2><div style="overflow-x:auto"><table><tr><th>set</th><th>board</th><th>matches supervisor</th><th>overtime min / day</th><th>rooms left undone</th><th>floor changes</th><th>reassignments</th><th>s / day</th></tr>{bench}</table></div>
</main></body></html>"""


if __name__ == "__main__":
    main()
