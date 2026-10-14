"""Minute-level housekeeping day: attendants work their board, events change the day, a policy
keeps the board up to date. Scores what a hotel feels: guests waiting, rooms late, overtime,
and how much the board was reshuffled.

The executor (what an attendant does next) is the same for every policy:
blocked early-check-in rooms first (by ETA), then front-desk refreshes, then departures, then
stayovers; nearest room breaks ties. Only the board differs.
"""
import math

from .plan import Board, generic_board, learn_order, learn_style, section_board, style_board, style_of
from .world import OPS, credits, task_minutes

INF = 10 ** 9


def walk(a, b):
    if a is None:
        return OPS["walk_floor"]
    if a["floor"] != b["floor"]:
        return OPS["walk_floor"]
    if a["wing"] != b["wing"]:
        return OPS["walk_wing"] + OPS["walk_room"] * 3
    return OPS["walk_room"] * abs(a["idx"] - b["idx"])


def prio(t):
    if t.get("blocked_eta") is not None:
        return (0, t["blocked_eta"])
    return ({"refresh": 1, "dep": 2, "stay": 3}[t["kind"]], 0)


class Sim:
    def __init__(self, case, morning, daytime, iters=4000):
        self.case, self.day = case, case["day"]
        self.rooms = self.day["rooms"]
        self.staff = case["staff"]
        self.tasks = {k: dict(v, status="pending") for k, v in self.day["tasks"].items()}
        self.on = list(case["on_shift"])
        self.style = learn_style(case["history"], len(self.staff))
        self.order = learn_order(case["history"], len(self.staff))
        if morning == "generic":
            b = generic_board(self.tasks, self.rooms, self.on)
        elif morning == "affinity":
            b = style_board(self.tasks, self.rooms, self.on, self.style, iters=iters, seed=self.day["seed"])
        else:
            b = section_board(self.tasks, self.rooms, self.on, self.order)
        self.assign = dict(b.of)
        self.morning = dict(b.of)
        self.morning_board = b.as_dict()
        self.daytime = daytime
        self.lazy = daytime == "lazy"
        self.att = {a: {"pos": None, "free": OPS["shift_start"], "cur": None, "lunch": False, "gone": False,
                        "last": None, "floor_changes": 0, "end": OPS["shift_start"], "work": 0.0} for a in self.on}
        self.moves = 0
        self.moved = set()
        self.known_until = {}       # late checkout approved until
        self.free_clean = {}
        for k in self.day["vacant_clean"]:
            self.free_clean.setdefault(self.rooms[k]["type"], []).append(k)
        self.early = []             # (task key or None, eta, wait if instant)
        self.nref = 0
        self.log = []

    # ---------- knowledge ----------
    def cleanable(self, k, t):
        """Truth: can the room be serviced at minute t."""
        x = self.tasks[k]
        if x["kind"] == "dep":
            return t >= x["out"]
        if x["kind"] == "refresh":
            return t >= x["created"]
        return x["dnd"] is None or t >= x["dnd"]

    def est_cleanable(self, k, t):
        """What the planner can know at t (no peeking at future checkouts or DND releases)."""
        x = self.tasks[k]
        if x["kind"] == "dep":
            if t >= x["out"]:
                return t
            return max(t, self.known_until.get(k, 630))
        if x["kind"] == "refresh":
            return t
        if x["dnd"] is None or t >= x["dnd"]:
            return t
        return None

    def active(self):
        return [a for a in self.on if not self.att[a]["gone"]]

    def pending_of(self, a):
        return [k for k, v in self.tasks.items() if v["status"] == "pending" and self.assign.get(k) == a]

    def dur(self, k, noisy=True):
        x = self.tasks[k]
        m = task_minutes(self.rooms[x["room"]], x["kind"])
        return m * (self.day["noise"].get(x["room"], 1.0) if noisy and x["kind"] != "refresh" else 1.0)

    # ---------- projection (deterministic, planner's knowledge) ----------
    def project(self, a, t, extra=None, drop=None):
        st = self.att[a]
        ks = [k for k in self.pending_of(a) if k != drop] + ([extra] if extra else [])
        clock = max(t, st["free"])
        pos = self.rooms[self.tasks[st["cur"]]["room"]] if st["cur"] else st["pos"]
        lunch = st["lunch"]
        ready = {}
        todo = [(k, self.est_cleanable(k, t)) for k in ks]
        todo = [(k, c) for k, c in todo if c is not None]
        while todo:
            if not lunch and clock >= OPS["lunch"][0]:
                clock += OPS["lunch"][1]
                lunch = True
            now = [(k, c) for k, c in todo if c <= clock]
            if not now:
                clock = min(c for _, c in todo)
                continue
            k, _ = min(now, key=lambda kc: (prio(self.tasks[kc[0]]), walk(pos, self.rooms[self.tasks[kc[0]]["room"]])))
            r = self.rooms[self.tasks[k]["room"]]
            clock += walk(pos, r) + self.dur(k, noisy=False)
            pos = r
            ready[k] = clock
            todo = [(j, c) for j, c in todo if j != k]
        return clock, ready

    # ---------- moves ----------
    def move(self, k, b, why):
        if self.assign.get(k) != b:
            self.assign[k] = b
            self.moves += 1
            self.moved.add(self.tasks[k]["room"])
            self.log.append((getattr(self, "now", 0), why, k, b))

    def floor_pen(self, b, room):
        floors = {self.rooms[self.tasks[j]["room"]]["floor"] for j in self.pending_of(b)}
        if room["floor"] in floors:
            return 0
        return 12 if any(abs(room["floor"] - f) == 1 for f in floors) else 25

    # ---------- day-time policies ----------
    def on_event(self, e, t):
        if self.daytime == "static":
            self.static(e, t)
        elif self.daytime in ("recut", "lazy"):
            self.recut(e, t)
        elif self.daytime == "free":
            self.free(e, t)
        else:
            self.repair(e, t)

    def min_load(self, exclude=None):
        def load(a):
            return sum(task_minutes(self.rooms[self.tasks[k]["room"]], self.tasks[k]["kind"]) for k in self.pending_of(a))
        return min((a for a in self.active() if a != exclude), key=load)

    def static(self, e, t):
        if e["type"] == "callout":
            orphans = sorted(self.pending_of(e["who"]), key=lambda k: self.tasks[k]["room"])
            for k in orphans:
                self.move(k, self.min_load(e["who"]), "callout")
        elif e["type"] == "refresh":
            owner = self.assign.get(e["room"])
            b = owner if owner in self.active() else self.min_load()
            self.move(e["key"], b, "refresh")

    def _refresh_owner(self, e):
        owner = self.assign.get(e["room"])
        self.assign[e["key"]] = owner if owner in self.active() else self.min_load()

    def recut(self, e, t):
        """Re-cut the learned sections over the rooms still to do, sized by each person's time left.
        Order of people never changes, so only rooms near section edges move."""
        if e["type"] == "refresh":
            self._refresh_owner(e)
        act = self.active()
        if e["type"] != "callout" and self.lazy:
            # only re-cut when someone is now projected to run past the shift
            if max(self.project(a, t)[0] for a in act) <= OPS["shift_end"]:
                return
        ks = {k: v for k, v in self.tasks.items() if v["status"] == "pending"}
        cap = {a: max(30, OPS["shift_end"] - max(t, self.att[a]["free"])) for a in act}
        b = section_board(ks, self.rooms, act, self.order, cap=cap)
        for k, a in b.of.items():
            self.move(k, a, "recut")

    def free(self, e, t):
        """Generic optimizer: rebuild the board from scratch on every event (affinity + local search)."""
        if e["type"] == "refresh":
            self._refresh_owner(e)
        ks = {k: v for k, v in self.tasks.items() if v["status"] == "pending"}
        act = self.active()
        cap = {a: max(30, OPS["shift_end"] - max(t, self.att[a]["free"])) for a in act}
        b = style_board(ks, self.rooms, act, self.style, cap=cap, iters=2500, seed=int(t))
        for k, a in b.of.items():
            self.move(k, a, "free")

    def repair(self, e, t):
        act = self.active()
        if e["type"] == "callout":
            orphans = sorted(self.pending_of(e["who"]), key=lambda k: prio(self.tasks[k]))
            for k in orphans:
                room = self.rooms[self.tasks[k]["room"]]

                def cost(b):
                    end0, _ = self.project(b, t)
                    end1, _ = self.project(b, t, extra=k)
                    return (end1 - end0) + self.floor_pen(b, room) - 2.0 * style_of(self.style, b, room) + 0.15 * max(0, end1 - OPS["shift_end"])
                self.move(k, min([b for b in act if b != e["who"]], key=cost), "callout")
        elif e["type"] == "early_checkin" and e.get("key"):
            k, eta = e["key"], e["eta"]
            owner = self.assign.get(k)
            _, rd = self.project(owner, t) if owner in act else (0, {})
            best, best_ready = owner, rd.get(k, INF)
            if best_ready > eta - 5:
                for b in act:
                    if b == owner:
                        continue
                    _, rb = self.project(b, t, extra=k)
                    if rb.get(k, INF) < best_ready - 10:
                        best, best_ready = b, rb[k]
                if best != owner:
                    self.move(k, best, "early_checkin")
        elif e["type"] == "refresh":
            k = e["key"]
            room = self.rooms[e["room"]]

            def ready(b):
                _, r = self.project(b, t, extra=k)
                return r.get(k, INF) + self.floor_pen(b, room) - 1.0 * style_of(self.style, b, room)
            self.move(k, min(act, key=ready), "refresh")
        self.rebalance(t)

    def pos_of(self, k):
        r = self.rooms[self.tasks[k]["room"]]
        return r["floor"] + (0.5 if r["wing"] == "B" else 0.0) + r["idx"] / 200

    def rebalance(self, t, max_moves=12):
        """Shift section edges: the latest-finishing attendant hands rooms from the edge of their
        section to the neighbour (in the learned floor order) who finishes earliest, one room at a time,
        only while it lowers the later of the two finish times. Rooms stay next to where the
        receiver already works, so the board still looks like the supervisor's."""
        act = sorted(self.active(), key=lambda a: (self.order.get(a, 0), a))
        for _ in range(max_moves):
            ends = {a: self.project(a, t)[0] for a in act}
            hi = max(act, key=ends.get)
            if ends[hi] <= OPS["shift_end"] - 10:
                return
            i = act.index(hi)
            best = None
            for j in (i - 1, i + 1):
                if not 0 <= j < len(act):
                    continue
                nb = act[j]
                cand = [k for k in self.pending_of(hi) if self.tasks[k].get("blocked_eta") is None and self.est_cleanable(k, t) is not None]
                if not cand:
                    continue
                # edge room: the one closest to the neighbour's side of the hotel
                k = min(cand, key=self.pos_of) if j < i else max(cand, key=self.pos_of)
                new_nb = self.project(nb, t, extra=k)[0]
                new_hi = self.project(hi, t, drop=k)[0]
                if max(new_nb, new_hi) < ends[hi] - 5 and (best is None or max(new_nb, new_hi) < best[0]):
                    best = (max(new_nb, new_hi), k, nb)
            if best is None:
                # no neighbour helps: give one room to whoever finishes first, preferring nearby floors
                lo = min((a for a in act if a != hi), key=ends.get, default=None)
                cand = [k for k in self.pending_of(hi) if self.tasks[k].get("blocked_eta") is None and self.est_cleanable(k, t) is not None]
                if lo is None or not cand or ends[hi] - ends[lo] < 40:
                    return
                k = min(cand, key=lambda k: self.floor_pen(lo, self.rooms[self.tasks[k]["room"]]))
                self.move(k, lo, "rebalance")
                continue
            self.move(best[1], best[2], "edge")

    # ---------- truth side of events ----------
    def apply(self, e, t):
        typ = e["type"]
        if typ == "late_checkout":
            self.known_until[e["room"]] = e["until"]
        elif typ == "early_departure":
            x = self.tasks[e["room"]]
            if x["status"] == "pending":
                x.update(kind="dep", out=t)
        elif typ == "refresh":
            self.nref += 1
            key = f"R{e['room']}#{self.nref}"
            self.tasks[key] = {"room": e["room"], "kind": "refresh", "created": t, "status": "pending", "out": None, "dnd": None}
            e["key"] = key
        elif typ == "callout":
            st = self.att[self.on[e["who"]]] if False else self.att[e["who"]] if e["who"] in self.att else None
            if st is None:
                return False
            st["gone"] = True
            if st["cur"]:
                self.tasks[st["cur"]]["status"] = "pending"
                st["cur"] = None
        elif typ == "early_checkin":
            pool = self.free_clean.get(e["rtype"], [])
            if pool:
                pool.pop()
                self.early.append({"key": None, "eta": e["eta"], "ready": t})
                return False
            cands = [k for k, v in self.tasks.items() if v["status"] != "done" and v["kind"] == "dep" and v.get("blocked_eta") is None
                     and self.rooms[v["room"]]["type"] == e["rtype"]]
            if not cands:
                self.early.append({"key": None, "eta": e["eta"], "ready": None})
                return False
            k = min(cands, key=lambda k: (self.est_cleanable(k, t), self.tasks[k]["room"]))
            self.tasks[k]["blocked_eta"] = e["eta"]
            e["key"] = k
            self.early.append({"key": k, "eta": e["eta"]})
        return True

    # ---------- run ----------
    def run(self):
        evs = [dict(e) for e in self.day["events"]]
        if evs and any(e["type"] == "callout" for e in evs):
            for e in evs:
                if e["type"] == "callout":
                    e["who"] = self.on[e["who"] % len(self.on)]
        ei = 0
        for t in range(OPS["shift_start"], OPS["hard_stop"] + 1):
            self.now = t
            for a in self.on:
                st = self.att[a]
                if st["cur"] and st["free"] <= t:
                    self.tasks[st["cur"]]["status"] = "done"
                    self.tasks[st["cur"]]["done_at"] = st["free"]
                    st["cur"] = None
            while ei < len(evs) and evs[ei]["t"] <= t:
                e = evs[ei]
                ei += 1
                if self.apply(e, t):
                    self.on_event(e, t)
            for a in self.on:
                st = self.att[a]
                if st["gone"] or st["cur"] or st["free"] > t:
                    continue
                if not st["lunch"] and t >= OPS["lunch"][0]:
                    st["lunch"] = True
                    st["free"] = t + OPS["lunch"][1]
                    continue
                cand = [k for k in self.pending_of(a) if self.cleanable(k, t)]
                if not cand:
                    continue
                k = min(cand, key=lambda k: (prio(self.tasks[k]), walk(st["pos"], self.rooms[self.tasks[k]["room"]])))
                r = self.rooms[self.tasks[k]["room"]]
                if st["pos"] is not None and st["pos"]["floor"] != r["floor"]:
                    st["floor_changes"] += 1
                d = walk(st["pos"], r) + self.dur(k)
                st.update(cur=k, free=t + d, pos=r, end=t + d)
                st["work"] += d
                self.tasks[k]["status"] = "in_progress"
        return self.metrics()

    def metrics(self):
        T = self.tasks
        done = {k: v.get("done_at") for k, v in T.items() if v["status"] == "done"}
        early_waits = []
        for x in self.early:
            if x["key"] is None:
                early_waits.append(0 if x.get("ready") is not None else OPS["hard_stop"] - x["eta"])
            else:
                rd = done.get(x["key"], OPS["hard_stop"] + 60)
                early_waits.append(max(0.0, rd - x["eta"]))
        # normal arrivals: earliest-ready free room of their type, first come first served
        blocked = {x["key"] for x in self.early if x["key"]}
        ready = {}
        for typ, ks in self.free_clean.items():
            ready.setdefault(typ, []).extend([0] * len(ks))
        for k, v in T.items():
            if v["kind"] == "dep" and k not in blocked:
                ready.setdefault(self.rooms[v["room"]]["type"], []).append(done.get(k, INF))
        for typ in ready:
            ready[typ].sort()
        waits, no_room = [], 0
        for a in sorted((a for a in self.day["arrivals"] if not a["early"]), key=lambda a: a["eta"]):
            pool = ready.get(a["type"], [])
            if not pool:
                no_room += 1
                continue
            r = pool.pop(0)
            if r >= INF:
                no_room += 1
            if a["eta"] <= 1020:
                waits.append(max(0.0, min(r, OPS["hard_stop"] + 60) - max(a["eta"], OPS["checkin"])))
        deps = [k for k, v in T.items() if v["kind"] == "dep"]
        late3 = sum(1 for k in deps if done.get(k, INF) > OPS["checkin"])
        undone = sum(1 for k, v in T.items() if v["status"] != "done" and not (v["kind"] == "stay" and v.get("dnd") == 9999))
        ot = sum(max(0.0, self.att[a]["end"] - OPS["shift_end"]) for a in self.on if not self.att[a]["gone"])
        return {
            "early_requests": len(early_waits),
            "early_waited_15": sum(w >= 15 for w in early_waits),
            "early_wait_min": round(sum(early_waits)),
            "guests_waited": sum(w > 0 for w in waits),
            "guest_wait_min": round(sum(waits)),
            "deps_late_3pm": late3,
            "undone": undone,
            "overtime_min": round(ot),
            "floor_changes": sum(self.att[a]["floor_changes"] for a in self.on),
            "moves": self.moves,
            "rooms_moved": len(self.moved),
            "rooms": len(self.day["tasks"]),
        }
