# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""Device settings: everything the pad holds that is not part of a profile.

One read covers the lot. Command **3** answers with capability and enabled bits
kept separate -- so the pad says both what it supports and what is on -- plus
sleep time, report rate and the two stick numbers:

    data[5]  supported   bit0 quick-switch config  bit1 Xbox home button
                         bit2 motion debounce      bit3 mapping switch
                         bit4 stick debounce       bit5 stick auto-calibration
                         bit6 stick rebound        bit7 status bar always on
    data[6]  enabled     same bit order
    data[7]  supported   bit0 always-on display    bit1 audio
    data[8]  enabled     same
    data[9]  sleep time (minutes)     data[10] report rate
    data[11] stick precision          data[12] stick sensitivity

The bit positions and the sub-ids of the write command are the same list read
twice: **sub-id N is bit N-1**, with 9 and 10 rolling over into the second pair
of bytes. `FEATURES` is that one list, and everything else here derives from it
rather than repeating it.

Writing splits in two. Command **19** is a generic "set feature N",
`[subId, value]`, and command **20**..**23** are standalone one-byte writes for
the four numeric settings. Command **29** restarts the pad and takes no
argument.

**A command-19 reply does not say which setting it belonged to.** Measured on a
wired Apex 5: `5a a5 13 01 00 <value> <crc>` -- the pad echoes the *value* where
Flydigi's own `IsAck` looks for the sub-id, so their check could never match and
ours copied it and reported a working command as failed. Nothing in the reply
identifies the setting, which makes an ACK mean "a setting was written" rather
than "this setting was written". When it matters which, read command 3 back;
`apply` does exactly that and is what the GUI and the CLI both go through.

What this pad does *not* have: **motion debounce** (sub 3) and **audio**
(sub 10) both come back unsupported on a k5, so neither is worth a control. The
**Xbox home button** (sub 2) is a third case and not a dead one -- supported, on,
and reachable on the wire, with only Flydigi's own wrapper declining to send it.
It is here and stays out of the app for good: Flydigi ship no UI for it on any
pad, and the models their capability list names are the ones whose Home button is
also the power button, which an Apex 5's is not. See docs/device-settings.md.
"""
from . import blobs

CMD_STATUS = 3
CMD_SETTING = 19
CMD_REPORT_RATE = 20
CMD_PRECISION = 21
CMD_SENSITIVITY = 22
CMD_SLEEP = 23
CMD_RESTART = 29

SUB_QUICK_SWITCH = 1
SUB_XBOX_HOME = 2
SUB_MOTION_DEBOUNCE = 3
SUB_MAPPING_SWITCH = 4
SUB_STICK_DEBOUNCE = 5
SUB_AUTO_CALIBRATION = 6
SUB_STICK_REBOUND = 7
SUB_STATUS_BAR = 8
SUB_OFF_SCREEN = 9
SUB_AUDIO = 10

# The one list. Order is the wire's -- sub-id N is bit N-1 -- so the reply
# decoder and the sub-command map are the same thing seen from two ends.
#
# `always_on` is the SDK's `OffScreen` bit under what it was measured to do:
# set, the stored picture stays up; clear, the panel goes dark. See
# `flydigi/screen.py:set_always_on` for why the SDK's name reads inverted.
FEATURES = (
    "quick_switch",
    "xbox_home",
    "motion_debounce",
    "mapping_switch",
    "stick_debounce",
    "auto_calibration",
    "stick_rebound",
    "status_bar_always_on",
    "always_on",
    "audio",
)

SUB_IDS = {name: index + 1 for index, name in enumerate(FEATURES)}

# Sleep time is a byte of minutes. Zero is Flydigi's "never", and the ceiling is
# what their own picker offers rather than what the byte holds.
SLEEP_NEVER = 0
SLEEP_MAX_MINUTES = 60

# `JoystickPrecision` is in **declaration order**, not by bit depth: 9-bit and
# 11-bit were added after 8/10/12, and 14/16 later still. So a pad reporting 2
# is at 10-bit, and any mapping that assumes the value climbs with resolution is
# wrong. Index 0 is the enum's `None`.
PRECISION_BITS = (None, 8, 10, 12, 9, 11, 14, 16)

# `JoystickSensitivity`, which is a *centre* sensitivity -- how hard the stick
# has to move off centre before it counts. Seven wire values; Space Station
# collapses them into three choices and we do not, because the pad distinguishes
# them and hiding four of them would only make the read-back unrepresentable.
SENSITIVITY_NAMES = {
    14: "Highest",
    15: "High",
    16: "Middle-high",
    17: "Middle",
    18: "Low-middle",
    19: "Low",
    20: "Lowest",
}
SENSITIVITY_VALUES = tuple(sorted(SENSITIVITY_NAMES))

# Flydigi's map, inverted -- theirs is Hz to wire value. **0 is not in it**, and
# 0 is what this pad reports. Both USB input endpoints declare a 1 ms interval
# on a full-speed device, where 1 ms is the shortest frame there is, so the pad
# is already at the ceiling and 0 reads as "default" rather than "unset". That
# is an inference from the descriptors, not a measurement, which is why
# `set_report_rate` says what it says.
REPORT_RATE_HZ = {1: 1000, 2: 500, 4: 250, 8: 125}
REPORT_RATE_DEFAULT = 0


class SettingsError(Exception):
    pass


# -- reading ---------------------------------------------------------------


def _bit(sub_id):
    """Where a sub-id's supported/enabled pair lives in a command-3 reply."""
    if sub_id <= 8:
        return 5, 1 << (sub_id - 1)
    return 7, 1 << (sub_id - 9)


def precision_name(value):
    """"10-bit", or the raw number when the pad answers outside the enum."""
    bits = PRECISION_BITS[value] if 0 <= value < len(PRECISION_BITS) else None
    return f"{bits}-bit" if bits else f"unknown ({value})"


def sensitivity_name(value):
    return SENSITIVITY_NAMES.get(value, f"unknown ({value})")


def report_rate_hz(value):
    """Polling rate in Hz, or None when the pad reports the undocumented 0."""
    return REPORT_RATE_HZ.get(value)


def parse_status(body):
    """A command-3 reply as a flat dict.

    Every feature contributes two keys -- `name` for the enabled bit and
    `name_usable` for the supported one -- because on this pad the difference is
    load-bearing: motion debounce and audio are unsupported, and a UI that read
    only the enabled bits would offer two controls the firmware ignores.
    """
    if len(body) < 13 or body[2] != CMD_STATUS:
        raise SettingsError("not a command 3 reply")
    state = {}
    for name in FEATURES:
        offset, mask = _bit(SUB_IDS[name])
        state[f"{name}_usable"] = bool(body[offset] & mask)
        state[name] = bool(body[offset + 1] & mask)
    state["sleep_minutes"] = body[9]
    state["report_rate"] = body[10]
    state["precision"] = body[11]
    state["sensitivity"] = body[12]
    return state


def read_status(ctrl, wait=0.5):
    """The whole settings block, in one exchange."""
    for body in blobs.replies(ctrl, blobs.build(CMD_STATUS), wait,
                              blobs.answers(CMD_STATUS)):
        if body[2] == CMD_STATUS:
            return parse_status(body)
    raise SettingsError("no reply to command 3 -- the pad may be asleep")


# -- writing ---------------------------------------------------------------


def set_feature(ctrl, sub_id, value, wait=0.5):
    """Write one command-19 sub-setting. True if the pad acknowledged it.

    The ACK is not proof that *this* setting moved -- see the module docstring.
    `apply` reads the block back, and callers who care should use that.
    """
    wanted = 1 if value else 0

    # Not `blobs.answers`, which stops at the command byte: the condition this
    # returns on is the byte *and* the echoed value, so the predicate has to
    # match both or a reply carrying the wrong value would end the collection
    # and turn a slow success into a reported failure. Stopping on exactly what
    # the caller was going to accept leaves the answer unchanged and only the
    # waiting shorter.
    def echoed(replies):
        reply = replies[-1]
        return (len(reply) > 7 and reply[3] == CMD_SETTING
                and reply[6] == wanted)

    return any(body[2] == CMD_SETTING and body[5] == wanted
               for body in blobs.replies(
                   ctrl, blobs.build(CMD_SETTING, bytes([sub_id, wanted])),
                   wait, echoed))


def _standalone(ctrl, cmd_id, value, wait):
    """One of the four `[4]=3, [5]=value` settings commands.

    Matched on the command byte alone. Command 19's reply is measured and puts
    the value where a success flag would go; nothing says these four do the
    same, and asserting a flag position that has never been read would turn a
    working write into a reported failure -- which is the exact bug command 19
    already produced once here.
    """
    return any(body[2] == cmd_id
               for body in blobs.replies(ctrl, blobs.build(cmd_id, bytes([value & 0xFF])),
                                         wait, blobs.answers(cmd_id)))


def set_sleep_minutes(ctrl, minutes, wait=0.5):
    """How long the pad idles before it sleeps. 0 is never.

    Worth having for its own sake: the pad ships at 15 minutes and does not go
    quiet when it sleeps, it leaves the USB bus -- so an idle pad and a dead
    cable look identical from here, and a session interrupted this way costs a
    reconnect and any unsaved config.
    """
    minutes = max(0, min(SLEEP_MAX_MINUTES, int(minutes)))
    return _standalone(ctrl, CMD_SLEEP, minutes, wait)


def set_precision(ctrl, value, wait=0.5):
    """Stick resolution, as a `JoystickPrecision` **index** -- not a bit count."""
    return _standalone(ctrl, CMD_PRECISION, value, wait)


def set_sensitivity(ctrl, value, wait=0.5):
    """Centre sensitivity, as one of the seven `JoystickSensitivity` values."""
    return _standalone(ctrl, CMD_SENSITIVITY, value, wait)


def set_report_rate(ctrl, value, wait=0.5):
    """Polling rate, as a wire value from `REPORT_RATE_HZ`.

    **Nothing here should call this without a reason.** This pad reports a rate
    of 0, which is not in Flydigi's map, so what a write does to it has never
    been observed -- and the pad is already polling at the 1 ms ceiling its
    endpoints declare, which leaves nothing to gain and a working rate to lose.
    Implemented because the command exists and a bench session may want it, and
    left out of the app for the same reason.
    """
    return _standalone(ctrl, CMD_REPORT_RATE, value, wait)


def restart(ctrl, wait=0.5):
    """Command 29. The pad reboots; its hidraw node goes away and comes back.

    Any handle open across this is stale afterwards -- reconnect rather than
    reusing one, the same as after a sleep.
    """
    return any(body[2] == CMD_RESTART
               for body in blobs.replies(ctrl, blobs.build(CMD_RESTART), wait,
                                         blobs.answers(CMD_RESTART)))


# -- write, then find out what really happened -----------------------------

# Which write each setting goes through, so one entry point covers the page.
# The four numeric ones are standalone commands; everything else is a bit.
_NUMERIC = {
    "sleep_minutes": (CMD_SLEEP, set_sleep_minutes),
    "precision": (CMD_PRECISION, set_precision),
    "sensitivity": (CMD_SENSITIVITY, set_sensitivity),
    "report_rate": (CMD_REPORT_RATE, set_report_rate),
}


def apply(ctrl, name, value, wait=0.5):
    """Write one setting by name and return the block as it reads afterwards.

    The read-back is the point. A command-19 ACK does not identify its setting,
    and the numeric commands are matched on their command byte alone, so the
    only honest answer to "did that work" is what command 3 says next.

    Raises `SettingsError` for a name that is not a setting -- a typo would
    otherwise write nothing and report the unchanged block as success.
    """
    if name in _NUMERIC:
        _NUMERIC[name][1](ctrl, value, wait)
    elif name in SUB_IDS:
        set_feature(ctrl, SUB_IDS[name], value, wait)
    else:
        raise SettingsError(f"no such setting: {name}")
    return read_status(ctrl, wait)


def describe(state):
    """The numeric fields as text, for a CLI or a label."""
    rate = report_rate_hz(state["report_rate"])
    return {
        "sleep": ("never" if state["sleep_minutes"] == SLEEP_NEVER
                  else f"{state['sleep_minutes']} min"),
        "report_rate": (f"{rate} Hz" if rate
                        else f"default ({state['report_rate']})"),
        "precision": precision_name(state["precision"]),
        "sensitivity": sensitivity_name(state["sensitivity"]),
    }
