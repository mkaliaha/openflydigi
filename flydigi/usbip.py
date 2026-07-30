# SPDX-FileCopyrightText: 2026 Mikalai Kaliaha
#
# SPDX-License-Identifier: MIT

"""A USB device implemented in userspace, attached to this machine's own kernel.

Pure Python, no dependencies, in the manner of uhid.py -- but where uhid can only
make a HID device, this makes a whole *USB* device: the kernel enumerates it,
binds ordinary drivers to every interface, and gives it a real sysfs path.

Why that matters is in docs/findings-haptics.md. Proton joins an audio endpoint
to a gamepad through a ContainerId that winepulse and winebus each derive
independently from the same `usb_device` -- so a virtual pad needs genuine USB
topology or it can never match. uhid has no USB parent and gets a random GUID.

The usual ways to fake USB topology are all walled off: `dummy_hcd` declares no
isochronous endpoint, `usbip-vudc` fails every iso URB with -EXDEV, and
FunctionFS rejects the audio class descriptors outright. What is left is the
USB/IP *client*: `vhci-hcd` is a virtual host controller that does implement
isochronous, ships as a module on every distro, and will happily enumerate a
device served by a local process.

We do not need the `usbip` userspace tool. Attaching is a sysfs write of
"port sockfd devid speed", and the kernel only requires the fd be a SOCK_STREAM
socket in the writing process's table -- it never checks the address family. So
an AF_UNIX socketpair works: no TCP, no port, nothing listening on a network.

Because the attach hands the kernel a socket that is already "connected", the
OP_REQ_IMPORT negotiation the usbip tool would have done is skipped entirely and
the conversation starts in the transfer phase. That is the whole protocol here:
USBIP_CMD_SUBMIT in, USBIP_RET_SUBMIT out.

Root is required for the sysfs write.
"""

import errno
import os
import socket
import struct
import subprocess
import threading

VHCI_SYSFS = "/sys/devices/platform/vhci_hcd.0"

# modprobe spells it with a dash, sysfs and lsmod with an underscore. Both names
# refer to the same module and asking for the wrong one fails as "not found",
# which reads as "this kernel does not have it".
MODULE = "vhci-hcd"

CMD_SUBMIT = 0x00000001
CMD_UNLINK = 0x00000002
RET_SUBMIT = 0x00000003
RET_UNLINK = 0x00000004

DIR_OUT = 0
DIR_IN = 1

SPEED_FULL = 2
SPEED_HIGH = 3

# Everything on the wire is big-endian.
#
# basic:  command seqnum devid direction ep
# submit: transfer_flags transfer_buffer_length start_frame number_of_packets
#         interval setup[8]
# ret:    status actual_length start_frame number_of_packets error_count pad[8]
# iso:    offset length actual_length status
_BASIC = struct.Struct(">IIIII")
_SUBMIT = struct.Struct(">Iiiii8s")
_RET = struct.Struct(">iiiii8x")
_UNLINK = struct.Struct(">I24x")
_ISO = struct.Struct(">IIIi")

# number_of_packets is a __s32, and carries -1 when the URB is not isochronous.
# Spelling it 0xFFFFFFFF is wrong twice over: it will not pack into a signed
# field, and it never compares equal to the -1 that comes back off the wire.
# Older kernels sent 0 instead, so the test is "> 0" rather than "!= NOT_ISO" --
# a real isochronous URB always carries at least one packet.
NOT_ISO = -1


class UsbIpError(Exception):
    pass


def _read_exactly(sock, count):
    """Read exactly count bytes, or raise if the peer hangs up mid-PDU."""
    chunks = []
    remaining = count
    while remaining:
        chunk = sock.recv(remaining)
        if not chunk:
            raise UsbIpError(f"peer closed with {remaining} of {count} bytes outstanding")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def module_loaded():
    """Whether vhci-hcd is loaded, asked of sysfs rather than of lsmod.

    The platform device is what the attach actually needs, so its presence is
    the honest test: a module listed by lsmod that failed to register would
    still answer "loaded" to anything reading /proc/modules.
    """
    return os.path.isdir(VHCI_SYSFS)


MODULES_ROOT = "/lib/modules"


def module_available(release=None, root=MODULES_ROOT):
    """Whether this kernel *has* the module, loaded or not.

    Read out of modules.dep rather than by running modinfo: it is one file, it
    needs no kmod in PATH, and it is the same list modprobe itself consults. The
    distinction matters because "not loaded" is a one-line fix and "not built
    for this kernel" is not -- and Fedora ships it, while a hardened or minimal
    kernel may not.

    `root` is a parameter so a test can point it at a tree it wrote; a distrobox
    needs no such help, since it mounts the host's /lib/modules and shares its
    kernel, so the default path is right in there too.
    """
    if module_loaded():
        return True
    release = release or os.uname().release
    try:
        with open(f"{root}/{release}/modules.dep") as handle:
            for line in handle:
                # Lines are "path/to/mod.ko[.xz]: deps...", so the module name
                # is bounded by a slash and a dot on the left-hand side only.
                path = line.split(":", 1)[0]
                if os.path.basename(path).split(".", 1)[0] == MODULE:
                    return True
    except OSError:
        return False
    return False


def load_module():
    """modprobe vhci-hcd. Needs root; returns whether it is loaded afterwards."""
    if module_loaded():
        return True
    try:
        subprocess.run(("modprobe", MODULE), capture_output=True, timeout=30)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return module_loaded()


def free_port(speed=SPEED_HIGH):
    """Return the number of a free vhci port of the right speed.

    The status table's `sta` column is the port state; 4 is VDEV_ST_NULL, i.e.
    nothing attached. High- and super-speed ports are separate ranges, and
    attaching a high-speed device to a super-speed port does not work.
    """
    want = "ss" if speed > SPEED_HIGH else "hs"
    try:
        with open(f"{VHCI_SYSFS}/status") as handle:
            rows = handle.read().splitlines()
    except FileNotFoundError:
        raise UsbIpError(
            "vhci-hcd is not loaded -- try: sudo modprobe vhci-hcd"
        ) from None

    for row in rows[1:]:
        fields = row.split()
        if len(fields) >= 4 and fields[0] == want and fields[2] == "004":
            return int(fields[1])
    raise UsbIpError(f"no free {want} port in {VHCI_SYSFS}/status")


class Device:
    """What a USB device has to answer. Subclass and override.

    Handlers return `bytes` for a successful IN transfer, `b""` to acknowledge an
    OUT or a zero-length transfer, or a negative errno to stall (-EPIPE is the
    usual "unsupported request").
    """

    speed = SPEED_HIGH

    def control(self, setup, data):
        """setup is the raw 8 bytes; data is the OUT payload, if any."""
        return -errno.EPIPE

    def transfer_in(self, ep, length):
        """An IN URB on a non-control endpoint.

        Return None to park it -- the kernel is asking for data we do not have
        yet, which is the normal state of an interrupt IN endpoint. Parked URBs
        complete later via Server.complete().
        """
        return None

    def transfer_out(self, ep, data):
        return b""

    def isochronous(self, ep, data, packets):
        """packets is a list of (offset, length, actual_length, status).

        Returns the same list, adjusted, plus IN data if the endpoint is IN.
        """
        return -errno.EPIPE


class Server:
    """Serves one Device to the local kernel over a socketpair."""

    def __init__(self, device, devid=0x00010002):
        self.device = device
        self.devid = devid
        self.port = None
        self._sock = None
        self._kernel_fd = None
        self._send_lock = threading.Lock()
        self._parked = {}  # seqnum -> (ep, length)
        self._parked_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self.error = None

    # -- attach / detach ----------------------------------------------------

    def attach(self, port=None, serve=True):
        """Hand the kernel one end of a socketpair. This is the privileged part.

        `serve=False` returns before the transfer thread starts, so a caller
        that means to drop privileges can do it in a single-threaded process --
        glibc's setuid applies to every thread, but doing it while one is
        already reading URBs is a race nobody needs to reason about. Call
        `start()` afterwards.
        """
        if os.geteuid() != 0:
            raise UsbIpError("attaching to vhci needs root")

        self.port = free_port(self.device.speed) if port is None else port
        ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
        self._sock, self._kernel_fd = ours, theirs

        # The kernel takes a reference to the struct file behind this fd, so it
        # stays valid after we stop referring to it here.
        line = f"{self.port} {theirs.fileno()} {self.devid} {self.device.speed}"
        try:
            with open(f"{VHCI_SYSFS}/attach", "w") as handle:
                handle.write(line)
        except OSError as exc:
            ours.close()
            theirs.close()
            raise UsbIpError(f"attach failed ({line!r}): {exc}") from None

        if serve:
            self.start()
        return self.port

    def start(self):
        """Begin answering URBs. Needs no privilege -- the socket is already ours."""
        if self._thread is not None:
            return
        self._thread = threading.Thread(target=self._serve, daemon=True)
        self._thread.start()

    def detach(self):
        """Take the device away, and do not depend on being allowed to.

        The sysfs write needs root and the serving process deliberately may not
        have it any more, so closing the socket is the mechanism and the write
        is only a courtesy: vhci's receive loop sees EOF, raises VDEV_EVENT_DOWN
        and resets the port to VDEV_ST_NULL by itself, which is exactly the
        state `free_port` looks for. Detaching by hand only makes it immediate.
        """
        self._stop.set()
        if self.port is not None:
            try:
                with open(f"{VHCI_SYSFS}/detach", "w") as handle:
                    handle.write(str(self.port))
            except OSError:
                pass
        for sock in (self._sock, self._kernel_fd):
            if sock is not None:
                try:
                    sock.close()
                except OSError:
                    pass
        self._sock = self._kernel_fd = None

    def __enter__(self):
        self.attach()
        return self

    def __exit__(self, *_):
        self.detach()

    # -- the transfer loop --------------------------------------------------

    def _send(self, payload):
        with self._send_lock:
            self._sock.sendall(payload)

    def _ret_submit(self, seqnum, ep, direction, status, data=b"", packets=None,
                    actual=None):
        body = b""
        if packets is None:
            # actual_length is bytes *transferred*, which for an OUT transfer is
            # what the host sent and we accepted -- not len(data), since an OUT
            # carries nothing back. Reporting 0 for an accepted 47-byte
            # SET_FEATURE makes the host see a short transfer and fail it with
            # EAGAIN, forever, which surfaced as Wine's
            #   err:hid:hidraw_device_set_feature_report id 8 write failed
            # retrying twice a second while the pad never finished configuring.
            actual, count, errors = (len(data) if actual is None else actual,
                                     NOT_ISO, 0)
        else:
            body = b"".join(_ISO.pack(*p) for p in packets)
            # For isochronous, actual_length is the total actually transferred
            # across all packets, not the size of any IN payload -- an OUT iso
            # URB carries no data back yet still moved bytes. error_count is how
            # many packets failed, which the host reports up to the audio layer.
            actual = sum(p[2] for p in packets)
            count = len(packets)
            errors = sum(1 for p in packets if p[3] != 0)
        header = _BASIC.pack(RET_SUBMIT, seqnum, self.devid, direction, ep)
        ret = _RET.pack(status, actual, 0, count, errors)
        self._send(header + ret + data + body)

    def complete(self, seqnum, data=b"", status=0):
        """Finish a URB that transfer_in() parked."""
        with self._parked_lock:
            parked = self._parked.pop(seqnum, None)
        if parked is None:
            return False
        ep, _length = parked
        self._ret_submit(seqnum, ep, DIR_IN, status, data)
        return True

    def complete_one(self, ep, data, status=0):
        """Finish the oldest URB parked on `ep`. Returns True if one was waiting.

        This is the normal way to push an input report: the kernel keeps a few
        interrupt IN URBs outstanding, and each report completes the oldest.
        """
        with self._parked_lock:
            for seqnum, (parked_ep, _length) in self._parked.items():
                if parked_ep == ep:
                    del self._parked[seqnum]
                    break
            else:
                return False
        self._ret_submit(seqnum, ep, DIR_IN, status, data)
        return True

    def _serve(self):
        try:
            while not self._stop.is_set():
                self._one_pdu()
        except BaseException as exc:  # noqa: BLE001 - recorded, then re-raised
            if not self._stop.is_set():
                # Without this the thread dies, the device silently never
                # enumerates, and the caller keeps happily pushing reports into
                # a socket nobody is reading.
                self.error = exc
                raise

    @property
    def failed(self):
        """The exception that killed the serve thread, or None."""
        if self.error is not None:
            return self.error
        if self._thread is not None and not self._thread.is_alive() \
                and not self._stop.is_set():
            return UsbIpError("serve thread exited without an exception")
        return None

    def _one_pdu(self):
        header = _read_exactly(self._sock, _BASIC.size)
        command, seqnum, _devid, direction, ep = _BASIC.unpack(header)

        if command == CMD_UNLINK:
            (victim,) = _UNLINK.unpack(_read_exactly(self._sock, _UNLINK.size))
            with self._parked_lock:
                cancelled = self._parked.pop(victim, None)
            # -ECONNRESET if we still held it; 0 if it had already completed.
            status = -errno.ECONNRESET if cancelled else 0
            # ret_unlink is one __s32 status; the rest of the 28 bytes is
            # padding, so zero it rather than reusing the submit field names.
            self._send(
                _BASIC.pack(RET_UNLINK, seqnum, self.devid, direction, ep)
                + _RET.pack(status, 0, 0, 0, 0)
            )
            return

        if command != CMD_SUBMIT:
            raise UsbIpError(f"unexpected command 0x{command:08x}")

        body = _read_exactly(self._sock, _SUBMIT.size)
        _flags, length, _start, n_packets, _interval, setup = _SUBMIT.unpack(body)

        is_iso = n_packets > 0
        out_data = b""
        if direction == DIR_OUT and length > 0:
            out_data = _read_exactly(self._sock, length)
        packets = None
        if is_iso:
            raw = _read_exactly(self._sock, n_packets * _ISO.size)
            packets = [
                _ISO.unpack_from(raw, i * _ISO.size) for i in range(n_packets)
            ]

        self._dispatch(seqnum, ep, direction, length, setup, out_data, packets)

    def _dispatch(self, seqnum, ep, direction, length, setup, out_data, packets):
        if packets is not None:
            result = self.device.isochronous(ep, out_data, packets)
            if isinstance(result, int):
                self._ret_submit(seqnum, ep, direction, result, b"", packets)
            else:
                data, packets = result
                self._ret_submit(seqnum, ep, direction, 0, data, packets)
            return

        if ep == 0:
            result = self.device.control(setup, out_data)
        elif direction == DIR_IN:
            result = self.device.transfer_in(ep, length)
            if result is None:
                with self._parked_lock:
                    self._parked[seqnum] = (ep, length)
                return
        else:
            result = self.device.transfer_out(ep, out_data)

        if isinstance(result, int):
            self._ret_submit(seqnum, ep, direction, result)
        elif direction == DIR_OUT:
            # Accepted the whole write. len(out_data) rather than `length`,
            # because a control OUT's transfer_buffer_length counts the data
            # stage only and the two agree, while trusting the header would
            # over-report if the host ever sent short.
            self._ret_submit(seqnum, ep, direction, 0, b"",
                             actual=len(out_data))
        else:
            # A device must never return more than the host asked for.
            self._ret_submit(seqnum, ep, direction, 0, result[:length])
