"""Process-wide shared transaction / exclusive migration permits (no TTL takeover)."""
import errno
import os
from workbench.execution.service_instance import ServiceInstanceLock


class MigrationBarrier:
    def __init__(self, path, *, exclusive=False):
        identity = ServiceInstanceLock(path)
        identity._check_identity()
        self.path = identity.database_path.with_name(identity.database_path.name + '.migration.lock')
        self.exclusive = exclusive
        self.file = None

    def __enter__(self):
        stage = 'system error'
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR, 0o600)
            try:
                os.set_inheritable(fd, False)
                self.file = os.fdopen(fd, 'r+b', buffering=0)
            except BaseException:
                os.close(fd)
                raise
            if os.name == 'nt':
                stage = 'native load failed'
                import ctypes
                from ctypes import wintypes
                import msvcrt
                self.kernel = ctypes.WinDLL('kernel32', use_last_error=True)
                stage = 'system error'
                class Overlapped(ctypes.Structure):
                    _fields_ = [('Internal', ctypes.c_size_t), ('InternalHigh', ctypes.c_size_t), ('Offset', wintypes.DWORD), ('OffsetHigh', wintypes.DWORD), ('hEvent', wintypes.HANDLE)]
                self.overlap = Overlapped()
                self.kernel.LockFileEx.argtypes = [wintypes.HANDLE,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,wintypes.DWORD,ctypes.POINTER(Overlapped)]
                self.kernel.LockFileEx.restype = wintypes.BOOL
                if not self.kernel.LockFileEx(msvcrt.get_osfhandle(fd), 1 | (2 if self.exclusive else 0), 0, 1, 0, ctypes.byref(self.overlap)):
                    code = ctypes.get_last_error()
                    if code == 33:  # ERROR_LOCK_VIOLATION
                        stage = 'lock contention; retry after active transactions/migration finish'
                    raise ctypes.WinError(code)
            elif os.name == 'posix':
                import fcntl
                try:
                    fcntl.flock(fd, (fcntl.LOCK_EX if self.exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
                except BlockingIOError as error:
                    if error.errno in (errno.EACCES, errno.EAGAIN):
                        stage = 'lock contention; retry after active transactions/migration finish'
                    raise
            else:
                raise RuntimeError('Unsupported migration lock OS')
        except BaseException as error:
            self.__exit__()
            if not isinstance(error, Exception):
                raise
            raise RuntimeError(f'Migration barrier unavailable: {stage}') from error
        return self

    def __exit__(self, *args):
        if self.file is not None:
            self.file.close()
            self.file = None
