"""Offline process fixture for real pipe/restart/resource tests."""
import json
import sys

for line in sys.stdin:
    request = json.loads(line)
    if request.get("exit"):
        break
    print(json.dumps({"echo": request}), flush=True)
