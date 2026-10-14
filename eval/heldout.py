"""Held-out days. Seeds and a harder config than dev, committed to git before the first run on them
and never used for tuning: bigger hotels, more call-outs, more early check-ins and DND."""

SEEDS = list(range(5001, 5041))

CFG = {"occupancy": (0.75, 0.97), "dnd": 0.15, "late_checkout": 0.10, "early_departure": 0.06,
       "early_checkin": 0.20, "refresh": (4, 8), "callout": 0.5, "floors": (6, 8)}
