#!/usr/bin/env python3
import json
import sys

# Read the JSON trace file
with open("traces/st_sync_v112_test.json", "r") as f:
    data = json.load(f)
    events = data["events"]

    time_events = {}
    for e in events:
        t = e["time"]
        et = e["event_type"]
        if t not in time_events:
            time_events[t] = []
        time_events[t].append({"time": t, "event_type": et, "task": e.get("task_name", "")})

    print("Time | Events")
    print("-" * 50)
    for t in sorted(time_events.keys(), key=int):
        stats = time_events[t]
        event_types = ", ".join([s["event_type"] for s in stats])
        print(f"{t:4} | {event_types}")
