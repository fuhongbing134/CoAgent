"""Windows read-only samples of this service and known no-descendant fixtures."""
import ctypes as C
from ctypes import wintypes as W
import os


class Memory(C.Structure):
    _fields_ = [("cb", W.DWORD), ("faults", W.DWORD)] + [
        (name, C.c_size_t) for name in ("peak", "rss", "quota_peak", "quota", "pool_peak", "pool", "page", "page_peak")]


class SystemMemory(C.Structure):
    _fields_ = [("length", W.DWORD), ("load", W.DWORD)] + [
        (name, C.c_ulonglong) for name in ("total", "available", "page_total", "page_available", "virtual_total", "virtual_available", "extended")]


def sample(pids):
    kernel = C.WinDLL("kernel32", use_last_error=True)
    psapi = C.WinDLL("psapi", use_last_error=True)
    kernel.OpenProcess.argtypes = [W.DWORD, W.BOOL, W.DWORD]
    kernel.OpenProcess.restype = W.HANDLE
    kernel.CloseHandle.argtypes = [W.HANDLE]
    kernel.GetProcessTimes.argtypes = [W.HANDLE] + [C.POINTER(W.FILETIME)]*4
    psapi.GetProcessMemoryInfo.argtypes = [W.HANDLE, C.POINTER(Memory), W.DWORD]
    system = SystemMemory()
    system.length = C.sizeof(system)
    if not kernel.GlobalMemoryStatusEx(C.byref(system)):
        raise C.WinError(C.get_last_error())
    rows, gaps = [], []
    for pid in {os.getpid(), *pids}:
        handle = kernel.OpenProcess(0x1000 | 0x10, False, pid)
        if not handle:
            gaps.append(pid)
            continue
        try:
            memory = Memory()
            memory.cb = C.sizeof(memory)
            times = [W.FILETIME() for _ in range(4)]
            if not psapi.GetProcessMemoryInfo(handle, C.byref(memory), memory.cb) or not kernel.GetProcessTimes(handle, *[C.byref(t) for t in times]):
                gaps.append(pid)
                continue
            seconds = sum((t.dwHighDateTime << 32) | t.dwLowDateTime for t in times[2:]) / 1e7
            rows.append(dict(pid=pid, rss=memory.rss, cpu_seconds=seconds))
        finally:
            kernel.CloseHandle(handle)
    return dict(processes=rows, gaps=gaps, available=system.available, rss=sum(r["rss"] for r in rows))
