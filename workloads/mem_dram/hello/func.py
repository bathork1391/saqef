# W2 memory-bound handler (runbook §30). The two arms differ ONLY in SAQEF_MEM_KIB:
#   workloads/mem_cache: 256 KiB per buffer (src + dst = 512 KiB, fits one core's L2)
#   workloads/mem_dram:  65536 KiB per buffer (128 MiB, 16x the 8 MiB L3: every pass goes to DRAM)
# Each call copies 256 KiB chunks src -> dst through a resident buffer for 5 ms of wall time,
# the same time budget as the CPU-bound spin, so function CPU per call is matched by design
# and only the memory traffic differs. memoryview slicing: no temporary copy, no allocation
# per chunk (musl/glibc would otherwise mmap/munmap 256 KiB per step).
import time

SAQEF_MEM_KIB = 65536
_N = SAQEF_MEM_KIB * 1024
_CH = min(_N, 256 * 1024)
_SRC = bytearray(b"\x5a") * _N
_DST = bytearray(_N)
_SRC_MV = memoryview(_SRC)
_pos = 0


def _sweep():
    global _pos
    t0 = time.perf_counter()
    while time.perf_counter() - t0 < 0.005:
        p = _pos
        _DST[p:p + _CH] = _SRC_MV[p:p + _CH]
        _pos = (p + _CH) % _N


def handler(ctx, data=None):
    _sweep()
    return {"message": "Hello World", "kib": SAQEF_MEM_KIB}
