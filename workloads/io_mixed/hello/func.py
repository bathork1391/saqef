# W5 I/O handler (runbook §36). The four arms differ ONLY in SAQEF_IO:
#   workloads/io_spin:  the call spins the CPU for 5 ms (the in-session CPU-bound baseline)
#   workloads/io_mixed: the call spins 2.5 ms, then waits 2.5 ms (time.sleep)
#   workloads/io_sleep: the call waits 5 ms (time.sleep), no kernel I/O work
#   workloads/io_disk:  the call overwrites the same 4 KiB at offset 0 of one file per process
#                       in the container's writable layer, then fsyncs it; the file never grows.
#                       /var/tmp, not /tmp: Fn mounts /tmp as tmpfs, where fsync is a no-op
#                       (probe 2026-10-15); Fn needs FN_DISABLE_READONLY_ROOTFS=1 for this arm
# The disk operation is fixed, not tuned to 5 ms: its wall time is whatever this host gives.
# The reply names the arm, the wait in microseconds and the filesystem _DIR sits on, so the
# unmeasured probe can check that fsync reaches the device on every platform.
import os
import time

SAQEF_IO = "mixed"
_BUF = b"\x5a" * 4096
_DIR = "/var/tmp"
_FD = os.open("%s/saqef_io_%d.bin" % (_DIR, os.getpid()), os.O_WRONLY | os.O_CREAT, 0o600) if SAQEF_IO == "disk" else None


def _fs_of(path):
    best, fs = "", "?"
    try:
        with open("/proc/self/mounts") as f:
            for line in f:
                p = line.split()
                if len(p) > 2 and (path == p[1] or path.startswith(p[1].rstrip("/") + "/")) and len(p[1]) > len(best):
                    best, fs = p[1], p[2]
    except OSError:
        pass
    return fs


_FS = _fs_of(_DIR)


def _io():
    t0 = time.perf_counter()
    if SAQEF_IO == "spin":
        while time.perf_counter() - t0 < 0.005:
            pass
    elif SAQEF_IO == "mixed":
        while time.perf_counter() - t0 < 0.0025:
            pass
        time.sleep(0.0025)
    elif _FD is None:
        time.sleep(0.005)
    else:
        os.pwrite(_FD, _BUF, 0)
        os.fsync(_FD)
    return "io=%s us=%d fs=%s" % (SAQEF_IO, (time.perf_counter() - t0) * 1e6, _FS)


def handler(ctx, data=None):
    return {"message": "Hello World", "io": _io()}
