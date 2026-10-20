import json
import subprocess

HELPER = "/usr/lib/localforge/hosts-helper"


def sync_hosts(projects):
    domains = [p["name"] + ".test" for p in projects]
    result = subprocess.run(
        ["pkexec", HELPER],
        input=json.dumps({"domains": domains}),
        capture_output=True,
        text=True,
        timeout=180,
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "Host update cancelled or denied")
    return result.stdout.strip()
