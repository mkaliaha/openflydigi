#!/usr/bin/env python3
"""Self-test for the DSX protocol listener. No controller or game required.

    python3 tests/test_dsx.py
"""
import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import dsx  # noqa: E402


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, buf, wait=0.0):
        # [3]=cmd, [5]=applyFlag, [6]=side, [7]=mode, [8..]=params
        self.sent.append((buf[3], buf[6], buf[7], list(buf[8:13])))
        return []


def packet(*instructions):
    return json.dumps({"instructions": list(instructions)}).encode("ascii")


def instr(type_, params):
    return {"type": type_, "parameters": params}


def check(name, condition, detail=""):
    print(f"  {'PASS' if condition else 'FAIL'}  {name}")
    if not condition and detail:
        print(f"        {detail}")
    return condition


def main():
    results = []

    # Canonical DSX trigger update: [index, side, 19, mode, params...]
    got = dsx.parse(packet(instr(1, [0, 2, 19, 2, 50, 1, 200, 1])))
    results.append(check("parses a TriggerUpdate instruction",
                         got == [(2, 2, [50, 1, 200, 1])], f"got {got}"))

    # Named instruction types -- Newtonsoft accepts them, so mods use them.
    got = dsx.parse(packet(instr("TriggerUpdate", [0, 1, 19, 1, 0, 1, 0, 0])))
    results.append(check("accepts named instruction types",
                         got == [(1, 1, [0, 1, 0, 0])], f"got {got}"))

    # Non-trigger instruction types are ignored, not misread.
    got = dsx.parse(packet(instr(2, [0, 255, 0, 0]), instr(3, [0, 1])))
    results.append(check("ignores RGB and PlayerLED instructions",
                         got == [], f"got {got}"))

    # Several instructions in one datagram.
    got = dsx.parse(packet(instr(1, [0, 1, 19, 1, 0, 1, 0, 0]),
                           instr(1, [0, 2, 19, 2, 5, 6, 7, 8])))
    results.append(check("handles multiple instructions per datagram",
                         got == [(1, 1, [0, 1, 0, 0]), (2, 2, [5, 6, 7, 8])], f"got {got}"))

    # Malformed input must not raise -- this is fed by third-party mods.
    bad = [b"", b"   ", b"not json", b"{}", b"[]", b'{"instructions":null}',
           b'{"instructions":[{"type":1}]}',
           b'{"instructions":[{"type":1,"parameters":[0,9,19,1]}]}',   # bad side
           b'{"instructions":[{"type":1,"parameters":[0,1]}]}',        # too short
           packet(instr(1, [0, 1, 19, "x", 1, 2, 3])) ]                # unparseable
    ok = True
    for raw in bad:
        try:
            if dsx.parse(raw) != []:
                ok = False
        except Exception as exc:  # noqa: BLE001
            ok = False
            print(f"        raised on {raw!r}: {exc}")
    results.append(check("malformed packets are ignored, never raise", ok))

    # Trailing NULs, as some senders pad datagrams.
    got = dsx.parse(packet(instr(1, [0, 2, 19, 2, 1, 2, 3, 4])) + b"\x00\x00")
    results.append(check("tolerates NUL-padded datagrams",
                         got == [(2, 2, [1, 2, 3, 4])], f"got {got}"))

    # Listener applies effects and suppresses identical repeats.
    rec = Recorder()
    listener = dsx.Listener(rec)
    data = packet(instr(1, [0, 2, 19, 2, 50, 1, 200, 1]))
    first = listener.handle(data)
    repeats = sum(listener.handle(data) for _ in range(9))
    results.append(check("effect applied once, repeats suppressed",
                         first == 1 and repeats == 0 and len(rec.sent) == 1,
                         f"first={first} repeats={repeats} sent={len(rec.sent)}"))

    # Wire layout: command 81, correct side/mode/params.
    cmd, side, mode, params = rec.sent[0]
    results.append(check("emits SetForceTrigger (81) with right side/mode/params",
                         cmd == 81 and side == 2 and mode == 2
                         and params[:4] == [50, 1, 200, 1],
                         f"cmd={cmd} side={side} mode={mode} params={params}"))

    # A changed effect on the same side does go through.
    listener.handle(packet(instr(1, [0, 2, 19, 1, 0, 1, 0, 0])))
    results.append(check("changed effect on same side is applied",
                         len(rec.sent) == 2, f"sent={len(rec.sent)}"))

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
