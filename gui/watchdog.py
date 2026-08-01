# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: GPL-3.0-or-later

"""What is the GUI thread doing while a frame is three hundred milliseconds late?

`qmlprofiler` cannot say. It records QML and JS ranges only, so Python running
in a queued cross-thread slot or a `QTimer` timeout leaves no mark in its
trace -- "the GUI thread has nothing to do" and "the GUI thread is doing
something the profiler cannot see" draw the same picture there. The measurement
in `docs/gui-performance-plan.md` (49 frames over 40 ms in 38 s, every one of
them stalled between `RenderThread:swap` and `GuiThread:polishAndSync`) is the
shape both explanations predict.

So the GUI thread stamps a heartbeat from a `BEAT_MS` timer, a daemon thread
watches how stale that stamp is, and when it is staler than the threshold every
thread's stack goes out through `faulthandler.dump_traceback(all_threads=True)`.
`polishAndSync` runs on the GUI thread's event loop, which is the loop the
heartbeat rides on, so a beat that is 200 ms late and a frame that is 200 ms
late are the same stall.

**Off unless `FLYDIGI_STALL_WATCHDOG` is set**, and armed from `gui/main.py`
before the engine exists. The value is `[<milliseconds>][:<path>]`: `30` reports
frames over 30 ms on stderr, `40:/tmp/stalls.txt` appends to a file instead, and
anything that does not begin with a number is taken as a path with the default
threshold. A threshold at or below `BEAT_MS` fires on nearly every beat.

**The dump is taken during the stall, not after it.** The trigger is the age of
the last beat while that beat is still the newest one -- never the gap between
two beats, which can only be measured once the thread has come back, by which
time the call that held it has returned and the stack names its caller or
nothing at all. While `dump_traceback` prints it holds the GIL, so no other
thread can execute a bytecode: a thread already inside a C call keeps running
there, but its Python frames cannot move underneath the dump. **Nothing is
written before the dump** for the same reason and it is not a stylistic choice:
a `write` drops the GIL, getting it back costs a switch interval, and in that
gap the stall can end and the dump name whatever ran next. So the label goes
underneath. Each stall reports once and the next report waits for a fresh beat,
so the banners count stalls rather than milliseconds.

**Two numbers, and only the second is worth a histogram.** The line directly
under a dump says how late the beat was when the dump was taken -- which is the
threshold plus a sampling slice every time, and says nothing except that this is
a stall, because the length of a stall is not knowable while you are still
inside it. The line after that is measured from the beat before the stall to the
first beat this loop *sees* after it, which is not quite the first beat there
was: the loop looks every `SLICE_S` and beats arrive every `BEAT_MS`, so it can
overstate the gap by about the two added together, and it overstates rather than
understates every time. Read it as a figure good to within roughly ten
milliseconds on stalls of forty and up, which is what it is for. A stall that
outlives the process has no second line.

**What a dump proves.** The GUI thread's deepest Python frame is where that
thread was at that instant, or the line from which it entered C. If it names
something under `gui/models/` or `gui/app.py`, then Python on the GUI thread is
holding the frame, and the frame is named.

**What it does not prove.** One dump is one sample of a stall, not its cause:
read fifty of them as a histogram and believe the shape rather than the single
worst one. A GUI thread whose deepest frame is the `qt_app.exec()` line of
`main()` is sitting in Qt's event loop with no Python above it -- `exec` is a C
call and has no frame of its own -- so no Python was running there at that
instant, and that is the answer that moves the search to
`QSGThreadedRenderLoop`, `polishAndSync` waiting on the render thread's swap,
and the compositor. It does not mean no Python was involved: a burst that ended
before the watchdog looked is invisible, and so is the difference between a
thread that was busy and a timer that was never delivered. The other threads are
worth reading for the same reason -- the device worker inside a blocking
`read()` has released the GIL, the same worker inside pure Python has not.

**Expect one report at startup.** The first beat is stamped when the watchdog
arms and the event loop does not run until `Main.qml` has loaded, so the first
stall reported is the window being built.
"""
import faulthandler
import os
import sys
import threading
import time

from PySide6.QtCore import Qt, QTimer

ENV = "FLYDIGI_STALL_WATCHDOG"

# Finer than a frame at 165 Hz, so what a report calls lateness is the stall and
# not the interval it was sampled at.
BEAT_MS = 4
LATE_MS = 30.0
# How long the watchdog sleeps between looks. A stall of the size being chased
# cannot hide inside it, and it keeps the tool's own share of the GIL small.
SLICE_S = 0.005

_timer = None
_beat = 0.0


def _stamp():
    global _beat
    _beat = time.monotonic()


def _watch(late_s, out):
    reported = 0.0
    while True:
        time.sleep(SLICE_S)
        beat = _beat
        overdue = time.monotonic() - beat
        if overdue < late_s or beat == reported:
            continue
        reported = beat
        # **Dump first, label afterwards.** Writing the banner ahead of the dump
        # reads better and is wrong: `out.write` reaches a `write(2)` with the
        # GIL dropped, and getting it back costs a full switch interval -- 5 ms
        # on this interpreter, which is a sixth of the smallest stall this looks
        # for. In that window the GUI thread can finish what it was doing, and
        # the dump then names whatever innocent frame came next. `dump_traceback`
        # holds the GIL for its whole run, so taking it first is the only
        # arrangement in which the stack is the stall's.
        faulthandler.dump_traceback(all_threads=True, file=out)
        out.write("=== stall: GUI thread was %.1f ms late above ===\n"
                  % (overdue * 1e3))
        out.flush()
        while _beat == reported:
            time.sleep(SLICE_S)
        out.write("=== stall over: the GUI thread ran again after %.1f ms ===\n"
                  % ((_beat - reported) * 1e3))
        out.flush()


def arm():
    """Start the watchdog if `FLYDIGI_STALL_WATCHDOG` asks for it.

    Call it once `QGuiApplication` exists and before the QML engine does: the
    heartbeat needs the GUI thread's event loop, and loading the window is
    itself worth watching. Returns the heartbeat timer, or None when the
    variable is unset -- the case that has to cost nothing, since every ordinary
    launch takes it.
    """
    global _timer
    value = os.environ.get(ENV, "").strip()
    if not value or _timer is not None:
        return None
    ms, _, path = value.partition(":")
    if ms and not ms.replace(".", "", 1).isdigit():
        ms, path = "", value    # a bare path is the obvious thing to type
    out = open(path, "a", buffering=1) if path else sys.stderr
    _stamp()
    _timer = QTimer()
    # A coarse timer may be grouped with another and fired late, which is
    # indistinguishable here from the thing being measured.
    _timer.setTimerType(Qt.PreciseTimer)
    _timer.timeout.connect(_stamp)
    _timer.start(BEAT_MS)
    threading.Thread(target=_watch, name="stall-watchdog", daemon=True,
                     args=((float(ms) if ms else LATE_MS) / 1e3, out)).start()
    return _timer
