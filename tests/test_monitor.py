#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Self-test for the XGameMonitor engine.

Spawns a real child process holding a known pointer chain in its own memory,
then reads it back through /proc/<pid>/mem -- so the chain walker is tested
against actual cross-process reads, not a mock.

    python3 tests/test_monitor.py
"""
import ctypes
import os
import re
import struct
import subprocess
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import monitor  # noqa: E402

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Child: builds base -> level1 -> level2 and prints the addresses, then waits.
CHILD = r"""
import ctypes, struct, sys, time
leaf = ctypes.create_string_buffer(16)
struct.pack_into('<Q', leaf, 8, 0xDEADBEEF)          # value at leaf+8
mid  = ctypes.create_string_buffer(16)
struct.pack_into('<Q', mid, 4, ctypes.addressof(leaf))   # pointer at mid+4
root = ctypes.create_string_buffer(16)
struct.pack_into('<Q', root, 0, ctypes.addressof(mid))   # pointer at root+0
print(ctypes.addressof(root), flush=True)
globals()['_keep'] = (leaf, mid, root)
time.sleep(60)
"""


def check(name, ok, detail=""):
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok and detail:
        print(f"        {detail}")
    return ok


class Recorder:
    def __init__(self):
        self.sent = []

    def send(self, buf, wait=0.0):
        self.sent.append((buf[6], buf[7], list(buf[8:13])))
        return []


def test_chain_against_real_process():
    proc = subprocess.Popen([sys.executable, "-c", CHILD], stdout=subprocess.PIPE, text=True)
    try:
        root_addr = int(proc.stdout.readline().strip())
        with monitor.MemoryReader(proc.pid) as reader:
            # offsets[0] is relative to the module base, so pass root as the base.
            value = monitor.read_chain(reader, root_addr, [0, 4, 8])
            ok = check("pointer chain read across a real process",
                       value == 0xDEADBEEF, f"got {value:#x}")
            # A broken first hop must yield 0, not raise.
            ok &= check("unreadable first hop returns 0",
                        monitor.read_chain(reader, 0x10, [0, 4, 8]) == 0)
            # Truncation to uint32.
            ok &= check("result truncated to uint32",
                        monitor.read_chain(reader, root_addr, [0, 4, 8]) <= 0xFFFFFFFF)
        return ok
    finally:
        proc.kill()
        proc.wait()


def test_real_configs():
    """Every shipped XGameMonitor config must load and be structurally sane."""
    import glob

    paths = sorted(glob.glob(os.path.join(ROOT, "work/monitor/*/mods/*/configs/*.json")))
    if not paths:
        print("  SKIP  no extracted configs found")
        return True
    ok = True
    for path in paths:
        name = os.path.basename(path)
        try:
            cfg = monitor.load_config(path)
        except Exception as exc:  # noqa: BLE001
            ok &= check(f"loads {name}", False, str(exc))
            continue
        has = bool(cfg.get("process_name")) and bool(cfg.get("vDefines"))
        chains_ok = all(d.get("offset") for d in cfg.get("vDefines") or [])
        ok &= check(f"loads {name} ({len(cfg.get('vFilters') or [])} filters)",
                    has and chains_ok)
    return ok


def test_condition_ops():
    values = {"item": 70000, "slot": 7}
    cases = [
        ({"use_define": "item", "op": "=", "value": 70000}, True),
        ({"use_define": "item", "op": "!=", "value": 70000}, False),
        ({"use_define": "item", "op": ">", "value": 69999}, True),
        ({"use_define": "item", "op": "<=", "value": 70000}, True),
        ({"use_define": "slot", "op": "in", "values": ["3", "7", "9"]}, True),
        ({"use_define": "slot", "op": "in", "values": ["3", "9"]}, False),
        # modNum quantises down to a multiple before comparing
        ({"use_define": "item", "op": "=", "value": 70000, "modNum": 100}, True),
        ({"use_define": "missing", "op": "=", "value": 1}, False),
    ]
    ok = True
    for item, expected in cases:
        got = monitor._condition_ok(item, values)
        ok &= check(f"condition {item.get('op')} {item.get('value', item.get('values'))}"
                    f"{' mod' + str(item['modNum']) if item.get('modNum') else ''}",
                    got == expected, f"got {got}")
    return ok


def test_engine_with_real_config():
    """Drive the Sekiro config with synthetic values and check effects fire."""
    path = os.path.join(ROOT, "work/monitor/035/mods/sekiro/configs/sekiro.json")
    if not os.path.exists(path):
        print("  SKIP  sekiro config not extracted")
        return True
    cfg = monitor.load_config(path)
    rec = Recorder()
    engine = monitor.Engine(cfg, rec)

    # No match yet -> trigger_default applied (left and right).
    engine.values = {"item": 1}
    engine.evaluate(["item"])
    default_ok = check("unmatched state applies trigger_default", len(rec.sent) == 2,
                       f"sent={len(rec.sent)}")

    # item 70000 is the first shuriken filter -> right trigger, mode 1.
    rec.sent.clear()
    engine.values = {"item": 70000}
    engine.evaluate(["item"])
    matched = [(s, m) for s, m, _p in rec.sent]
    filter_ok = check("known item value selects its filter",
                      (2, 1) in matched, f"sent={matched}")

    # Repeating the same state must not resend.
    rec.sent.clear()
    engine.evaluate(["item"])
    dedup_ok = check("unchanged effect not resent", len(rec.sent) == 0,
                     f"sent={len(rec.sent)}")
    return default_ok and filter_ok and dedup_ok


def main():
    print("chain walker:")
    r1 = test_chain_against_real_process()
    print("condition operators:")
    r2 = test_condition_ops()
    print("shipped configs:")
    r3 = test_real_configs()
    print("engine:")
    r4 = test_engine_with_real_config()
    ok = all([r1, r2, r3, r4])
    print(f"\n{'all passed' if ok else 'FAILURES'}")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
