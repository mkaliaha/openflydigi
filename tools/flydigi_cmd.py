#!/usr/bin/env python3

# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Flydigi Apex 5 vendor command tool (Linux hidraw).

Packet framing (from decompiled Flydigi.Basic AbstractCommand.CreateSimpleCommand):
    [0] report id   (0x03 on the wired vendor interface)
    [1] 0x5A
    [2] 0xA5
    [3] command id
    [4] payload length
    [5..] payload
Total 32 bytes. "CRC" is an 8-bit sum over a command-specific range.

Safety: trigger effects drive real servos. Keep force low and duration short.
"""
import argparse
import fcntl
import glob
import os
import select
import struct
import sys
import time

VID = 0x37D7
PID = 0x2501
PACKET_LEN = 32
MAGIC1 = 0x5A
MAGIC2 = 0xA5
DEFAULT_REPORT_ID = 0x03

CMD_GET_INFO = 0x01
CMD_SET_FORCE_TRIGGER = 81
CMD_SET_FORCE_TRIGGER_GRIP = 82
CMD_K6_TRIGGER_MODE = 83
CMD_K6_TRIGGER_WAVEFORM = 85
CMD_K6_TRIGGER_REALTIME = 87

SIDE = {"left": 1, "right": 2, "both": 3}


def checksum(buf, start, end):
    """8-bit sum over [start, end). Matches Flydigi ByteExtension.Crc."""
    return sum(buf[start:end]) & 0xFF


def find_vendor_interface():
    """Return hidraw path whose report descriptor has a 31-byte output report."""
    candidates = []
    for path in sorted(glob.glob("/dev/hidraw*")):
        uevent = f"/sys/class/hidraw/{os.path.basename(path)}/device/uevent"
        try:
            with open(uevent) as fh:
                text = fh.read()
        except OSError:
            continue
        if f"{VID:08X}" not in text.upper().replace("0000", "0000"):
            if f"{VID:04X}" not in text.upper():
                continue
        # vendor-defined usage page 0xffa0 shows up as 06 a0 ff at descriptor start
        desc_path = f"/sys/class/hidraw/{os.path.basename(path)}/device/report_descriptor"
        try:
            with open(desc_path, "rb") as fh:
                desc = fh.read()
        except OSError:
            continue
        if desc[:3] == b"\x06\xa0\xff":
            candidates.append(path)
    return candidates[0] if candidates else None


def build(cmd_id, payload, report_id=DEFAULT_REPORT_ID):
    buf = bytearray(PACKET_LEN)
    buf[0] = report_id
    buf[1] = MAGIC1
    buf[2] = MAGIC2
    buf[3] = cmd_id
    buf[4] = len(payload)
    buf[5 : 5 + len(payload)] = payload
    return buf


def drain(fd):
    """Discard replies that arrived before we asked anything.

    hidraw hands every reply to every reader, so the desktop app's 30-second
    poll lands here too -- and did, during the trigger-effect tests: a Get info
    ACK turned up in the middle of a rumble exchange. Called under the lock, so
    whatever is waiting belongs to an exchange that has finished.
    """
    while True:
        ready, _, _ = select.select([fd], [], [], 0)
        if not ready:
            return
        try:
            if not os.read(fd, 64):
                return
        except BlockingIOError:
            return


def send(fd, buf, wait=0.4, quiet=False):
    # Same advisory lock flydigi/device.py takes, so this tool and the app take
    # turns instead of talking over each other. Steam is not excluded and must
    # not be -- the vendor interface works with Steam Input on.
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        drain(fd)
        if not quiet:
            print(f"  TX {bytes(buf).hex(' ')}")
        os.write(fd, bytes(buf))
        deadline = time.time() + wait
        replies = []
        while time.time() < deadline:
            remaining = deadline - time.time()
            ready, _, _ = select.select([fd], [], [], max(0.0, remaining))
            if not ready:
                continue
            data = os.read(fd, 64)
            if data:
                replies.append(data)
                if not quiet:
                    print(f"  RX {data.hex(' ')}")
        if not replies and not quiet:
            print("  RX (no response)")
        return replies
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def cmd_info(fd, args):
    print("[get info] cmd 0x01")
    send(fd, build(CMD_GET_INFO, b"", args.report_id))


def cmd_listen(fd, args):
    print(f"[listen] {args.seconds}s of input reports")
    deadline = time.time() + args.seconds
    while time.time() < deadline:
        ready, _, _ = select.select([fd], [], [], 0.5)
        if ready:
            data = os.read(fd, 64)
            if data:
                print(f"  RX {data.hex(' ')}")


def cmd_normal(fd, args):
    """SetForceTrigger: Normal (effect off)."""
    side = SIDE[args.side]
    params = bytes([side, 0])
    payload = bytes([1]) + params  # applyFlag=1, then params
    print(f"[force trigger: normal] side={args.side}")
    send(fd, build(CMD_SET_FORCE_TRIGGER, payload, args.report_id))


def cmd_race(fd, args):
    """SetForceTrigger: Race (constant resistance past a travel point)."""
    side = SIDE[args.side]
    resistance = max(1, args.resistance)
    params = bytes([side, 1, args.stroke, resistance, 1 if args.match_stroke else 0])
    payload = bytes([1]) + params
    print(
        f"[force trigger: race] side={args.side} stroke={args.stroke} "
        f"resistance={resistance}"
    )
    send(fd, build(CMD_SET_FORCE_TRIGGER, payload, args.report_id))


def cmd_sniper(fd, args):
    """SetForceTrigger: Sniper (vibrates once held past a point)."""
    params = bytes([SIDE[args.side], 2, args.stroke, max(1, args.pressure),
                    max(1, args.strength), max(1, args.frequency),
                    1 if args.match_stroke else 0])
    print(f"[force trigger: sniper] side={args.side} stroke={args.stroke}")
    send(fd, build(CMD_SET_FORCE_TRIGGER, bytes([1]) + params, args.report_id))


def cmd_recoil(fd, args):
    """SetForceTrigger: Recoil (a band of resistance that gives way).

    The zero before the match flag is a slot Flydigi's builder leaves empty.
    """
    params = bytes([SIDE[args.side], 3, args.stroke, args.travel,
                    max(1, args.resistance), 0, 1 if args.match_stroke else 0])
    print(f"[force trigger: recoil] side={args.side} stroke={args.stroke} "
          f"travel={args.travel}")
    send(fd, build(CMD_SET_FORCE_TRIGGER, bytes([1]) + params, args.report_id))


def cmd_lock(fd, args):
    """SetForceTrigger: Lock (a hard stop at a travel point)."""
    params = bytes([SIDE[args.side], 4, args.stroke, args.strength,
                    1 if args.match_stroke else 0])
    print(f"[force trigger: lock] side={args.side} stroke={args.stroke}")
    send(fd, build(CMD_SET_FORCE_TRIGGER, bytes([1]) + params, args.report_id))


def cmd_vibrate(fd, args):
    """SetForceTrigger: Vibration (mode 5). **Does nothing on an Apex 5.**

    Kept for protocol work, not because it is useful. The pad ACKs it and the
    triggers seat themselves as it applies, but nothing follows -- tested
    against `sniper`, whose parameters are byte-identical, with the pad's
    standing rumble bind suppressed. Use `bind` for rumble in the triggers and
    `sniper` for a vibration of their own.

    Note that testing this without suppressing the bind first will look like it
    works, because the pad binds rumble to its triggers at rest:

        flydigi_cmd.py bind both --filter 255 --scale 0 --params "0,0,0,0"
    """
    params = bytes([SIDE[args.side], 5, args.stroke, max(1, args.pressure),
                    max(1, args.strength), max(1, args.frequency),
                    1 if args.match_stroke else 0])
    print(f"[force trigger: vibration] side={args.side} stroke={args.stroke}")
    send(fd, build(CMD_SET_FORCE_TRIGGER, bytes([1]) + params, args.report_id))


def cmd_k6mode(fd, args):
    """K6TriggerMode: set trigger + grip mode."""
    buf = build(CMD_K6_TRIGGER_MODE, b"", args.report_id)
    buf[4] = 4
    buf[5] = args.trigger_mode
    buf[6] = args.grip_mode
    buf[7] = checksum(buf, 3, 3 + buf[4])
    print(f"[k6 mode] trigger={args.trigger_mode} grip={args.grip_mode}")
    send(fd, buf)


def cmd_k6realtime(fd, args):
    """K6TriggerRealtime: 8 samples of (trigger, lgrip, rgrip)."""
    buf = build(CMD_K6_TRIGGER_REALTIME, b"", args.report_id)
    buf[4] = 28
    buf[5] = args.channel
    for i in range(8):
        base = 6 + i * 3
        buf[base] = args.trigger
        buf[base + 1] = args.lgrip
        buf[base + 2] = args.rgrip
    buf[30] = checksum(buf, 1, 30)
    print(f"[k6 realtime] trigger={args.trigger} lgrip={args.lgrip} rgrip={args.rgrip}")
    send(fd, buf)


def cmd_bind(fd, args):
    """SetForceTrigger SyncWithGrip (cmd 82): bind trigger haptics to rumble.

    payload = [side, bindType, filter, scale, stroke, pressureLevel, strength, frequency]
    """
    vib = [int(x) for x in args.params.split(",")]
    if len(vib) < 4:
        sys.exit("--params needs at least 4 values (stroke,pressure,strength,frequency)")
    sides = [SIDE[args.side]] if args.side != "both" else [1, 2]
    for side in sides:
        buf = build(CMD_SET_FORCE_TRIGGER_GRIP, b"", args.report_id)
        buf[4] = 11
        payload = [side, args.bindtype, args.filter, args.scale] + vib[:4]
        buf[5 : 5 + len(payload)] = bytes(payload)
        print(f"[bind grip] side={side} payload={payload}")
        send(fd, buf)


def cmd_rumble(fd, args):
    """Haptic command 0x12 (per SDL): [4]=6, [5]=low, [6]=high."""
    buf = build(0x12, b"", args.report_id)
    buf[4] = 6
    buf[5] = args.low
    buf[6] = args.high
    print(f"[rumble] low={args.low} high={args.high} for {args.seconds}s")
    send(fd, buf, wait=0.1)
    time.sleep(args.seconds)
    off = build(0x12, b"", args.report_id)
    off[4] = 6
    send(fd, off, wait=0.1)
    print("[rumble] stopped")


def cmd_game(fd, args):
    """Apply a game's Tier-1 vibration binding straight from gamelist.json."""
    import json

    with open(args.gamelist) as fh:
        data = json.load(fh)["data"]
    matches = [
        g
        for g in data
        if args.name.lower()
        in ((g.get("enGameName") or "") + " " + (g.get("gameName") or "")).lower()
    ]
    if not matches:
        sys.exit(f"no game matching {args.name!r}")
    g = matches[0]
    print(f"game: {g.get('enGameName')}  (id={g.get('id')})")
    print(
        f"  vibType={g.get('vibType')} vibFilter={g.get('vibFilter')} "
        f"pwmScal={g.get('pwmScal')} vibParams={g.get('vibParams')!r}"
    )
    if not g.get("isVibration"):
        print("  WARNING: this game is not vibration-type; binding anyway")

    for side_name, side_id, pkey, fkey, skey in (
        ("left", 1, "vibParams", "vibFilter", "pwmScal"),
        ("right", 2, "vibParamsRight", "vibFilterRight", "pwmScalRight"),
    ):
        raw = g.get(pkey) or g.get("vibParams") or ""
        if not raw:
            print(f"  {side_name}: no params, skipped")
            continue
        vib = [int(x) for x in raw.split(",")]
        buf = build(CMD_SET_FORCE_TRIGGER_GRIP, b"", args.report_id)
        buf[4] = 11
        payload = [
            side_id,
            g.get("vibType") or 0,
            g.get(fkey) or 0,
            g.get(skey) or 0,
        ] + vib[:4]
        buf[5 : 5 + len(payload)] = bytes(payload)
        print(f"  [{side_name}] payload={payload}")
        send(fd, buf)


def cmd_raw(fd, args):
    payload = bytes.fromhex(args.payload) if args.payload else b""
    buf = build(args.cmd, payload, args.report_id)
    if args.sum_range:
        start, end, pos = (int(x, 0) for x in args.sum_range.split(","))
        buf[pos] = checksum(buf, start, end)
    print(f"[raw] cmd={args.cmd}")
    send(fd, buf)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--device", help="hidraw path (default: autodetect)")
    ap.add_argument(
        "--report-id", type=lambda x: int(x, 0), default=DEFAULT_REPORT_ID
    )
    sub = ap.add_subparsers(dest="command", required=True)

    sub.add_parser("info").set_defaults(func=cmd_info)

    p = sub.add_parser("listen")
    p.add_argument("--seconds", type=float, default=5.0)
    p.set_defaults(func=cmd_listen)

    p = sub.add_parser("normal")
    p.add_argument("side", choices=SIDE)
    p.set_defaults(func=cmd_normal)

    p = sub.add_parser("race")
    p.add_argument("side", choices=SIDE)
    p.add_argument("--stroke", type=int, default=50)
    p.add_argument("--resistance", type=int, default=40)
    p.add_argument("--match-stroke", action="store_true", default=True)
    p.set_defaults(func=cmd_race)

    # Defaults are the gentle ones the profile editor starts an effect on.
    p = sub.add_parser("sniper")
    p.add_argument("side", choices=SIDE)
    p.add_argument("--stroke", type=int, default=50)
    p.add_argument("--pressure", type=int, default=25)
    p.add_argument("--strength", type=int, default=20)
    p.add_argument("--frequency", type=int, default=20)
    p.add_argument("--match-stroke", action="store_true", default=True)
    p.set_defaults(func=cmd_sniper)

    p = sub.add_parser("recoil")
    p.add_argument("side", choices=SIDE)
    p.add_argument("--stroke", type=int, default=50)
    p.add_argument("--travel", type=int, default=30)
    p.add_argument("--resistance", type=int, default=40)
    p.add_argument("--match-stroke", action="store_true", default=True)
    p.set_defaults(func=cmd_recoil)

    p = sub.add_parser("lock")
    p.add_argument("side", choices=SIDE)
    p.add_argument("--stroke", type=int, default=60)
    p.add_argument("--strength", type=int, default=255)
    p.add_argument("--match-stroke", action="store_true", default=True)
    p.set_defaults(func=cmd_lock)

    p = sub.add_parser("vibrate")
    p.add_argument("side", choices=SIDE)
    p.add_argument("--stroke", type=int, default=50)
    p.add_argument("--pressure", type=int, default=25)
    p.add_argument("--strength", type=int, default=20)
    p.add_argument("--frequency", type=int, default=20)
    p.add_argument("--match-stroke", action="store_true", default=True)
    p.set_defaults(func=cmd_vibrate)

    p = sub.add_parser("k6mode")
    p.add_argument("--trigger-mode", type=int, default=2, help="0=Local 1=BindGrip 2=Realtime")
    p.add_argument("--grip-mode", type=int, default=1, help="0=RotorMapping 1=Realtime")
    p.set_defaults(func=cmd_k6mode)

    p = sub.add_parser("k6realtime")
    p.add_argument("--channel", type=int, default=1)
    p.add_argument("--trigger", type=int, default=40)
    p.add_argument("--lgrip", type=int, default=0)
    p.add_argument("--rgrip", type=int, default=0)
    p.set_defaults(func=cmd_k6realtime)

    p = sub.add_parser("bind")
    p.add_argument("side", choices=SIDE)
    p.add_argument("--bindtype", type=int, default=2)
    p.add_argument("--filter", type=int, default=8)
    p.add_argument("--scale", type=int, default=10)
    p.add_argument("--params", default="1,25,20,20")
    p.set_defaults(func=cmd_bind)

    p = sub.add_parser("rumble")
    p.add_argument("--low", type=int, default=200)
    p.add_argument("--high", type=int, default=200)
    p.add_argument("--seconds", type=float, default=2.0)
    p.set_defaults(func=cmd_rumble)

    p = sub.add_parser("game")
    p.add_argument("name")
    p.add_argument("--gamelist", default="gamelist.json")
    p.set_defaults(func=cmd_game)

    p = sub.add_parser("raw")
    p.add_argument("cmd", type=lambda x: int(x, 0))
    p.add_argument("--payload", default="")
    p.add_argument("--sum-range", help="start,end,pos for checksum byte")
    p.set_defaults(func=cmd_raw)

    args = ap.parse_args()

    path = args.device or find_vendor_interface()
    if not path:
        sys.exit("no Flydigi vendor interface found")
    print(f"device: {path}  report id: 0x{args.report_id:02x}")

    fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)
    try:
        args.func(fd, args)
    finally:
        os.close(fd)


if __name__ == "__main__":
    main()
