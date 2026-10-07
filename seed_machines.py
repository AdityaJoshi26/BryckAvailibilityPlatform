#!/usr/bin/env python3
"""
Seed the Bryck inventory from machines.json via the REST API.

Data is read from machines.json (already extracted from the hardware doc),
NOT from the .doc file — so this script has no document dependencies and
needs no third-party packages (uses only the standard library).

Usage:
    python3 seed_machines.py                      # uses default API below
    python3 seed_machines.py http://HOST:8000     # override API base
    BRYCK_API=http://HOST:8000 python3 seed_machines.py
"""
import json
import os
import sys
import urllib.request
import urllib.error

# API host given by the user. Override via argv[1] or BRYCK_API env var.
DEFAULT_API = "http://182.168.0.165:8000"

HERE = os.path.dirname(os.path.abspath(__file__))
DATA_FILE = os.path.join(HERE, "machines.json")


def api_base() -> str:
    if len(sys.argv) > 1 and sys.argv[1].strip():
        return sys.argv[1].rstrip("/")
    return os.environ.get("BRYCK_API", DEFAULT_API).rstrip("/")


def post_machine(base: str, machine: dict):
    """POST one machine. Returns (status_code, body_text)."""
    url = f"{base}/inventory"
    data = json.dumps(machine).encode("utf-8")
    req = urllib.request.Request(
        url, data=data, method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            return resp.status, resp.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def main() -> int:
    base = api_base()
    with open(DATA_FILE, "r", encoding="utf-8") as f:
        machines = json.load(f)["machines"]

    print(f"Target API : {base}")
    print(f"Machines   : {len(machines)} from machines.json\n")

    created = skipped = failed = 0
    for m in machines:
        ip = m.get("ip_address", "<no-ip>")
        try:
            code, body = post_machine(base, m)
        except urllib.error.URLError as e:
            print(f"  ERROR   {ip:<16} cannot reach API: {e.reason}")
            print("\nAborting — check the API host/port and that the server is running.")
            return 1

        if code == 201:
            created += 1
            print(f"  CREATED {ip:<16} {m.get('hostname', '')}")
        elif code == 409:
            skipped += 1
            print(f"  EXISTS  {ip:<16} (already in inventory, skipped)")
        else:
            failed += 1
            print(f"  FAILED  {ip:<16} HTTP {code}: {body[:160]}")

    print("\n" + "-" * 48)
    print(f"Created: {created}   Already existed: {skipped}   Failed: {failed}")
    return 0 if failed == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
