"""A stand-in external checker for tests: speaks the docs/KERNEL.md line
protocol, answering with the Python reference checker. FAKE_KERNEL picks a
behaviour: ok (default), no_messages, allow_all, garbage, error, slow,
crash, wrong_contract."""

import json
import os
import sys
import time

from biject_surject import ir

MODE = os.environ.get("FAKE_KERNEL", "ok")
policies = {}


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


for line in sys.stdin:
    msg = json.loads(line)
    kind = msg.get("type")
    if kind == "hello":
        send({"type": "hello", "checker": "fake-kernel@1",
              "contract": 99 if MODE == "wrong_contract" else 1})
    elif kind == "load":
        policies[msg["policy_id"]] = msg["policy"]
        send({"type": "loaded", "policy_id": msg["policy_id"]})
    elif kind == "check":
        if MODE == "garbage":
            sys.stdout.write("not json\n")
            sys.stdout.flush()
            continue
        if MODE == "error":
            send({"type": "error", "message": "proof search failed"})
            continue
        if MODE == "slow":
            time.sleep(30)
        if MODE == "crash":
            sys.exit(3)
        verdict = ir.check_request(policies[msg["policy_id"]], msg["request"])
        verdict["checker"] = "fake-kernel@1"
        verdict["evidence"] = {"proof": "sha256:feed"}
        if MODE == "no_messages":
            for v in verdict["violations"]:
                v.pop("message", None)
        if MODE == "allow_all":
            verdict["decision"], verdict["violations"] = "allow", []
        send({"type": "verdict", "verdict": verdict})
