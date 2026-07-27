#!/usr/bin/env python3
"""Self-test for the Forza telemetry parser and rule engine.

Builds synthetic Data Out packets and checks the shipped Flydigi rules fire the
effects they should. No controller and no game required.

    python3 tests/test_forza.py
"""
import os
import struct
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from flydigi import forza  # noqa: E402

CONFIG = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "configs", "forza.json"
)


def make_packet(length=324, **values):
    """Build a Data Out datagram with the given DataPacket fields set."""
    buf = bytearray(length)
    shift = forza.BUFFER_OFFSETS[length]
    values.setdefault("IsRaceOn", 1.0)
    for name, value in values.items():
        kind, offset, shifted = forza.FIELDS[name]
        size, fmt = forza._READERS[kind]
        struct.pack_into(fmt, buf, offset + (shift if shifted else 0), value)
    return bytes(buf)


class Recorder:
    """Stands in for a Controller, capturing packets instead of writing them."""

    def __init__(self):
        self.sent = []

    def send(self, buf, wait=0.0):
        # payload layout: [4]=10, [5]=applyFlag, [6]=side, [7]=mode, [8..]=params
        self.sent.append((buf[3], buf[6], buf[7], list(buf[8:13])))
        return []


def run_case(name, packets, expect=None, forbid=None):
    """Check that (side, mode) `expect` fires, and/or that `forbid` does not.

    Other rules may legitimately fire alongside -- e.g. the regain-traction rule
    matches whenever slip is near zero -- so cases assert on a specific effect
    rather than on the absence of all output.
    """
    cfg = forza.load_config(CONFIG)
    rec = Recorder()
    engine = forza.Engine(cfg, rec)
    for raw in packets:
        parsed = forza.parse(raw)
        assert parsed is not None, "packet failed to parse"
        engine.feed(parsed)
    got = [(side, mode) for _cmd, side, mode, _p in rec.sent]
    ok = True
    if expect is not None:
        ok = ok and expect in got
    if forbid is not None:
        ok = ok and forbid not in got
    print(f"  {'PASS' if ok else 'FAIL'}  {name}")
    if not ok:
        print(f"        expected={expect} forbid={forbid} got={got}")
    return ok


def main():
    if not os.path.exists(CONFIG):
        print("  SKIP  configs/forza.json missing -- run tools/fetch-configs")
        return 0

    results = []

    # Format detection
    assert forza.parse(make_packet(324)) is not None
    assert forza.parse(make_packet(311)) is not None
    assert forza.parse(bytes(232)) is None, "sled format should be ignored"
    assert forza.parse(bytes(99)) is None, "unknown size should be ignored"
    print("  PASS  packet format detection (324 / 311 accept, 232 / junk reject)")
    results.append(True)

    # Field decoding, including the 12-byte shift on 324-byte packets
    p = forza.parse(make_packet(324, Gear=4, Accelerator=200, Speed=42.5))
    assert p["Gear"] == 4, p["Gear"]
    assert p["Accelerator"] == 200
    assert abs(p["Speed"] - 42.5) < 0.01
    p311 = forza.parse(make_packet(311, Gear=6, Speed=10.0))
    assert p311["Gear"] == 6 and abs(p311["Speed"] - 10.0) < 0.01
    print("  PASS  field decoding with buffer offset (324 and 311)")
    results.append(True)

    # Gear shift -> right trigger, mode 2. Needs Gear changed AND Accelerator > 0.
    results.append(run_case(
        "gear shift fires on right trigger",
        [make_packet(Gear=2, Accelerator=180), make_packet(Gear=3, Accelerator=180)],
        (2, 2),
    ))

    # Same gear change but throttle closed -> the gear-shift effect must not fire.
    results.append(run_case(
        "gear shift suppressed with throttle closed",
        [make_packet(Gear=2, Accelerator=0), make_packet(Gear=3, Accelerator=0)],
        forbid=(2, 2),
    ))

    # High-speed braking -> left trigger, mode 2 (Speed > 25 and Brake changed).
    results.append(run_case(
        "high-speed braking fires on left trigger",
        [make_packet(Speed=40.0, Brake=0, Gear=3),
         make_packet(Speed=40.0, Brake=120, Gear=3)],
        (1, 2),
    ))

    # Loss of traction -> right trigger, mode 2, via the 'or' fold across slip fields.
    results.append(run_case(
        "loss of traction fires on right trigger",
        [make_packet(Gear=3, TireSlipRatioRearRight=0.9)],
        (2, 2),
    ))

    # Effects persist, so an unchanged effect must not be resent.
    cfg = forza.load_config(CONFIG)
    rec = Recorder()
    engine = forza.Engine(cfg, rec)
    for _ in range(10):
        engine.feed(forza.parse(make_packet(Gear=3, TireSlipRatioRearRight=0.9)))
    dedup_ok = len(rec.sent) == 1
    print(f"  {'PASS' if dedup_ok else 'FAIL'}  identical effect sent once, not per packet"
          f" ({len(rec.sent)} write(s))")
    results.append(dedup_ok)

    print(f"\n{sum(results)}/{len(results)} passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
