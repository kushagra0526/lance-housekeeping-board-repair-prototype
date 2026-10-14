"""Synthetic hotel, one housekeeping day with its live events, and a hidden supervisor whose
boards the planner has to learn from.

All operating numbers are assumptions in OPS (stated in the README), not Lance data.
Times are minutes after midnight.
"""
import math
import random

OPS = {
    "shift_start": 480,    # 08:00
    "shift_end": 990,      # 16:30
    "hard_stop": 1080,     # 18:00 - work past this is left undone
    "lunch": (720, 30),    # 12:00, 30 min
    "dep_min": 30,         # departure clean (1.0 credit)
    "stay_min": 17,        # stayover service (0.5 credit, "refresh every room every night")
    "suite_mult": 1.5,
    "refresh_min": 12,     # front-desk refresh request
    "noise_cv": 0.18,      # lognormal spread of clean times
    "walk_room": 0.4,      # per room-number step on the same wing
    "walk_wing": 3,        # change wing on a floor
    "walk_floor": 6,       # elevator / stairs incl. cart
    "checkin": 900,        # 15:00
    "checkout": 660,       # 11:00
    "labor_util": 0.92,    # staffing: credits fill 92% of paid minutes
}
TYPES = (("king", 0.55), ("queen", 0.35), ("suite", 0.10))


def hm(t):
    return f"{int(t) // 60:02d}:{int(t) % 60:02d}"


def make_hotel(rng, floors):
    rooms = []
    for f in range(2, 2 + floors):
        n = rng.randint(18, 28)
        for k in range(1, n + 1):
            t = rng.random()
            typ = "king" if t < 0.55 else "queen" if t < 0.9 else "suite"
            rooms.append({"id": f * 100 + k, "floor": f, "wing": "A" if k <= n // 2 else "B", "idx": k, "type": typ})
    return rooms


def make_staff(rng, hotel, n_staff):
    """Roster with each attendant's hidden home floors/wing (the supervisor's habit, not given to the planner)."""
    floors = sorted({r["floor"] for r in hotel})
    names = ["Maria", "Ana", "Rosa", "Lupe", "Mei", "Fatima", "Olga", "Grace", "Nadia", "Tess", "Ivy", "Joy", "Lena", "Pia"]
    homes = [floors[(i * len(floors)) // n_staff] for i in range(n_staff)]
    rng.shuffle(homes)  # roster order (seniority, alphabet) says nothing about who works where
    staff = []
    for i in range(n_staff):
        home = homes[i]
        staff.append({"name": names[i], "home": home, "wing": rng.choice("AB"), "suites": rng.random() < 0.7})
    return staff


def task_minutes(room, kind):
    base = {"dep": OPS["dep_min"], "stay": OPS["stay_min"], "refresh": OPS["refresh_min"]}[kind]
    return base * (OPS["suite_mult"] if room["type"] == "suite" and kind != "refresh" else 1)


def make_day(seed, hotel, staff, cfg):
    """Rooms to service this morning + the day's events (truth from the generator)."""
    rng = random.Random(seed)
    occ = rng.uniform(*cfg["occupancy"])
    occupied = [r for r in hotel if rng.random() < occ]
    occ_ids = {r["id"] for r in occupied}
    vacant_clean = [r["id"] for r in hotel if r["id"] not in occ_ids]
    dep_frac = rng.uniform(0.35, 0.55)
    tasks, events = {}, []
    for r in occupied:
        if rng.random() < dep_frac:
            # actual checkout time; the morning board only knows "due out"
            u = rng.random()
            t = rng.uniform(420, 540) if u < 0.3 else rng.uniform(540, OPS["checkout"])
            tasks[r["id"]] = {"room": r["id"], "kind": "dep", "out": t, "dnd": None}
        else:
            dnd = None
            if rng.random() < cfg["dnd"]:
                dnd = rng.uniform(600, 840) if rng.random() < 0.5 else 9999  # released at, or all day
            tasks[r["id"]] = {"room": r["id"], "kind": "stay", "out": None, "dnd": dnd}
    deps = [k for k, v in tasks.items() if v["kind"] == "dep"]
    stays = [k for k, v in tasks.items() if v["kind"] == "stay"]
    # late checkouts approved during the morning
    for k in rng.sample(deps, int(len(deps) * cfg["late_checkout"])):
        new = rng.choice([780, 840])
        events.append({"t": rng.uniform(480, 600), "type": "late_checkout", "room": k, "until": new})
        tasks[k]["out"] = max(tasks[k]["out"], new - rng.uniform(0, 20))
    # stayovers that leave a day early -> become departures
    for k in rng.sample(stays, int(len(stays) * cfg["early_departure"])):
        t = rng.uniform(510, 690)
        events.append({"t": t, "type": "early_departure", "room": k})
        tasks[k] = {"room": k, "kind": "stay", "out": None, "dnd": None, "becomes_dep_at": t}
    # DND placements known at board time are part of the morning state; releases are events
    for k in stays:
        d = tasks[k].get("dnd")
        if d and d < 9999:
            events.append({"t": d, "type": "dnd_off", "room": k})
    # arrivals: early check-in requests (with a blocked due-out room) and normal arrivals
    rooms_by = {r["id"]: r for r in hotel}
    n_arr = int(len(deps) * rng.uniform(0.85, 1.1)) + int(len(vacant_clean) * 0.3)
    arrivals = []
    for _ in range(n_arr):
        typ = rng.choices([t for t, _ in TYPES], [w for _, w in TYPES])[0]
        if rng.random() < cfg["early_checkin"]:
            eta = rng.uniform(600, 810)
            req = rng.uniform(450, min(eta - 60, 660))
            arrivals.append({"type": typ, "eta": eta, "early": True, "req": req})
        else:
            u = rng.random()
            eta = rng.uniform(900, 1020) if u < 0.35 else rng.uniform(1020, 1320)
            arrivals.append({"type": typ, "eta": eta, "early": False})
    for a in arrivals:
        if a["early"]:
            events.append({"t": a["req"], "type": "early_checkin", "rtype": a["type"], "eta": a["eta"]})
    for _ in range(rng.randint(*cfg["refresh"])):
        k = rng.choice(stays)
        events.append({"t": rng.uniform(570, 900), "type": "refresh", "room": k})
    if rng.random() < cfg["callout"]:
        events.append({"t": rng.uniform(600, 780), "type": "callout", "who": rng.randrange(1000)})  # index into the on-shift list
    events.sort(key=lambda e: e["t"])
    durations = {}
    for k in tasks:
        durations[k] = math.exp(rng.gauss(0, OPS["noise_cv"]))
    return {"seed": seed, "tasks": tasks, "events": events, "arrivals": arrivals, "vacant_clean": vacant_clean,
            "noise": durations, "rooms": rooms_by, "rng_seed": rng.randrange(10 ** 9)}


def credits(task, room):
    c = 1.0 if task["kind"] == "dep" else 0.5
    return c * (OPS["suite_mult"] if room["type"] == "suite" else 1)


def n_attendants(tasks, rooms):
    mins = sum(task_minutes(rooms[k], v["kind"]) for k, v in tasks.items())
    paid = OPS["shift_end"] - OPS["shift_start"] - OPS["lunch"][1]
    return max(3, math.ceil(mins / (paid * OPS["labor_util"])))


def supervisor_board(tasks, rooms, staff, on_shift):
    """The hidden supervisor: walks the hotel floor by floor, wing by wing, and hands out
    equal-credit sections to the attendants ordered by their home floor and wing. Compact, balanced,
    and personal (who gets which floor) - the planner only sees the boards this produces."""
    order = sorted(tasks, key=lambda k: (rooms[k]["floor"], rooms[k]["wing"], rooms[k]["idx"]))
    people = sorted(on_shift, key=lambda a: (staff[a]["home"], staff[a]["wing"], a))
    tot = sum(credits(tasks[k], rooms[k]) for k in order)
    per = tot / len(people)
    board = {a: [] for a in on_shift}
    acc, i = 0.0, 0
    for k in order:
        if acc >= per * (i + 1) - 0.25 and i < len(people) - 1:
            i += 1
        board[people[i]].append(k)
        acc += credits(tasks[k], rooms[k])
    return board


def history(hotel, staff, cfg, seeds):
    """Past boards the supervisor built (the only style signal the planner gets)."""
    out = []
    for s in seeds:
        day = make_day(s, hotel, staff, cfg)
        n = min(len(staff), n_attendants(day["tasks"], day["rooms"]))
        on = sorted(random.Random(s * 3).sample(range(len(staff)), n))
        out.append({"board": supervisor_board(day["tasks"], day["rooms"], staff, on), "rooms": day["rooms"]})
    return out


DEV_CFG = {"occupancy": (0.7, 0.95), "dnd": 0.10, "late_checkout": 0.08, "early_departure": 0.05,
           "early_checkin": 0.15, "refresh": (3, 6), "callout": 0.35, "floors": (5, 7)}


def make_case(seed, cfg=DEV_CFG):
    """Hotel + roster + 20 past supervisor boards + the test day (supervisor is off)."""
    rng = random.Random(seed)
    hotel = make_hotel(rng, rng.randint(*cfg["floors"]))
    probe = make_day(seed * 7 + 1, hotel, [], cfg)
    n_need = n_attendants(probe["tasks"], probe["rooms"])
    staff = make_staff(rng, hotel, n_need + 3)
    hist = history(hotel, staff, cfg, [seed * 1000 + i for i in range(20)])
    day = make_day(seed * 7 + 1, hotel, staff, cfg)
    n = min(len(staff), n_attendants(day["tasks"], day["rooms"]))
    on = sorted(random.Random(seed * 11).sample(range(len(staff)), n))
    return {"hotel": hotel, "staff": staff, "history": hist, "day": day, "on_shift": on,
            "supervisor": supervisor_board(day["tasks"], day["rooms"], staff, on)}
