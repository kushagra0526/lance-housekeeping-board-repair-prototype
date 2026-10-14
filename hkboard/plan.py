"""Morning board: learn the supervisor's style from past boards, then build a balanced, compact board.

Style = for each attendant, how often the supervisor gave them each floor-wing (Laplace-smoothed
log share). The planner never sees the supervisor's rules, only past boards.
"""
import math
import random
from collections import defaultdict

from .world import OPS, credits, task_minutes

W = {"bal": 4.0, "floor": 1.5, "wing": 0.5, "style": 0.35}


def learn_style(history, n_staff, alpha=0.5):
    cnt = defaultdict(lambda: defaultdict(float))
    keys = set()
    for h in history:
        for a, rooms in h["board"].items():
            for k in rooms:
                r = h["rooms"][k]
                cnt[a][(r["floor"], r["wing"])] += 1
                keys.add((r["floor"], r["wing"]))
    style = {}
    for a in range(n_staff):
        tot = sum(cnt[a].values()) + alpha * len(keys)
        style[a] = {fw: math.log((cnt[a][fw] + alpha) / tot) for fw in keys}
    return style


def style_of(style, a, room):
    s = style.get(a) if style else None
    return s.get((room["floor"], room["wing"]), -6.0) if s else 0.0


class Board:
    """Assignment room->attendant with per-attendant cost terms kept incrementally."""

    def __init__(self, tasks, rooms, staff_ids, style=None, cap=None):
        self.tasks, self.rooms, self.ids, self.style = tasks, rooms, list(staff_ids), style
        self.cap = cap or dict.fromkeys(self.ids, 1.0)
        self.of = {}
        self.by = {a: set() for a in self.ids}
        tot_c = sum(self.cred(k) for k in tasks)
        tot_cap = sum(self.cap.values()) or 1
        self.target = {a: tot_c * self.cap[a] / tot_cap for a in self.ids}

    def cred(self, k):
        t = self.tasks[k]
        if t["kind"] == "refresh":
            return OPS["refresh_min"] / OPS["dep_min"]
        return credits(t, self.rooms[t["room"]])

    def room(self, k):
        return self.rooms[self.tasks[k]["room"]]

    def assign(self, k, a):
        old = self.of.get(k)
        if old is not None:
            self.by[old].discard(k)
        self.of[k] = a
        self.by[a].add(k)

    def cost_a(self, a):
        ks = self.by[a]
        load = sum(self.cred(k) for k in ks)
        floors = {self.room(k)["floor"] for k in ks}
        fws = {(self.room(k)["floor"], self.room(k)["wing"]) for k in ks}
        sty = sum(style_of(self.style, a, self.room(k)) for k in ks) if self.style else 0.0
        return (W["bal"] * (load - self.target[a]) ** 2 + W["floor"] * max(0, len(floors) - 1)
                + W["wing"] * (len(fws) - len(floors)) - W["style"] * sty)

    def cost(self):
        return sum(self.cost_a(a) for a in self.ids)

    def as_dict(self):
        return {a: sorted(self.by[a]) for a in self.ids}


def generic_board(tasks, rooms, staff_ids):
    """The 'generic split': rooms in number order cut into equal-credit sections."""
    b = Board(tasks, rooms, staff_ids)
    order = sorted(tasks, key=lambda k: tasks[k]["room"])
    tot = sum(b.cred(k) for k in order)
    acc, i = 0.0, 0
    per = tot / len(b.ids)
    for k in order:
        if acc >= per * (i + 1) and i < len(b.ids) - 1:
            i += 1
        b.assign(k, b.ids[i])
        acc += b.cred(k)
    return b


def learn_order(history, n_staff):
    """Where in the hotel each attendant usually works: credit-weighted mean position
    (floor + wing) over past boards. Attendants with no history sit in the middle."""
    pos, w = defaultdict(float), defaultdict(float)
    for h in history:
        for a, rooms in h["board"].items():
            for k in rooms:
                r = h["rooms"][k]
                pos[a] += r["floor"] + (0.5 if r["wing"] == "B" else 0.0) + r["idx"] / 200
                w[a] += 1
    mid = sum(pos.values()) / max(1, sum(w.values()))
    return {a: (pos[a] / w[a] if w[a] else mid) for a in range(n_staff)}


def section_board(tasks, rooms, staff_ids, order, cap=None):
    """Walk the hotel floor by floor, wing by wing, and cut credit-balanced sections, handing them
    out in the learned order (who usually works where). cap scales each person's share."""
    b = Board(tasks, rooms, staff_ids, cap=cap)
    people = sorted(b.ids, key=lambda a: (order.get(a, 0), a))
    seq = sorted(tasks, key=lambda k: (b.room(k)["floor"], b.room(k)["wing"], b.room(k)["idx"]))
    acc, i = 0.0, 0
    bound = 0.0
    for k in seq:
        while i < len(people) - 1 and acc >= bound + b.target[people[i]] - 0.25:
            bound += b.target[people[i]]
            i += 1
        b.assign(k, people[i])
        acc += b.cred(k)
    return b


def style_board(tasks, rooms, staff_ids, style, cap=None, iters=0, seed=0, init=None, order=None):
    """Kept for the ablation: a free-form board from floor-wing affinities + local search."""
    b = Board(tasks, rooms, staff_ids, style, cap)
    seq = sorted(tasks, key=lambda k: (b.room(k)["floor"], b.room(k)["wing"], b.room(k)["idx"]))
    load = dict.fromkeys(b.ids, 0.0)
    for k in seq:
        r = b.room(k)
        a = max(b.ids, key=lambda a: style_of(style, a, r) - 3.0 * max(0.0, load[a] + b.cred(k) - b.target[a]))
        b.assign(k, a)
        load[a] += b.cred(k)
    local_search(b, iters, seed)
    return b


def local_search(b, iters, seed):
    rng = random.Random(seed)
    keys = list(b.of)
    if len(b.ids) < 2 or not keys:
        return b
    for _ in range(iters):
        if rng.random() < 0.6:
            k = rng.choice(keys)
            a = b.of[k]
            fl = b.room(k)["floor"]
            near = [x for x in b.ids if x != a and any(b.room(j)["floor"] == fl for j in b.by[x])]
            c = rng.choice(near or [x for x in b.ids if x != a])
            before = b.cost_a(a) + b.cost_a(c)
            b.assign(k, c)
            if b.cost_a(a) + b.cost_a(c) > before - 1e-9:
                b.assign(k, a)
        else:
            k1, k2 = rng.sample(keys, 2)
            a1, a2 = b.of[k1], b.of[k2]
            if a1 == a2:
                continue
            before = b.cost_a(a1) + b.cost_a(a2)
            b.assign(k1, a2)
            b.assign(k2, a1)
            if b.cost_a(a1) + b.cost_a(a2) > before - 1e-9:
                b.assign(k1, a1)
                b.assign(k2, a2)
    return b


def agreement(board, ref):
    """Share of rooms given to the same attendant as in the reference (supervisor's) board."""
    who = {k: a for a, ks in ref.items() for k in ks}
    n = sum(1 for a, ks in board.items() for k in ks if who.get(k) == a)
    return n / max(1, len(who))


def minutes_of(tasks, rooms, ks):
    return sum(task_minutes(rooms[tasks[k]["room"]], tasks[k]["kind"]) for k in ks)
