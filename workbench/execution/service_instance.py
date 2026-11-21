"""Non-preemptible OS lock for one execution service per local database."""
import os
from pathlib import Path


class ServiceInstanceLock:
    def __init__(self, database_path):
        self.database_path = Path(os.path.normcase(str(Path(database_path).expanduser().resolve())))
        self._file = None

    def _check_identity(self):
        if self.database_path.exists():
            stat = self.database_path.stat()
            if not self.database_path.is_file() or stat.st_nlink != 1:
                raise RuntimeError('Database must be a regular file with exactly one hard link')
        if not self.database_path.parent.is_dir():
            raise RuntimeError('Database parent directory must already exist')

    def __enter__(self):
        if self._file is not None:
            raise RuntimeError('Instance lock is already held by this object')
        self._check_identity()
        lock_path = self.database_path.with_name(self.database_path.name + '.service.lock')
        fd = os.open(lock_path, os.O_RDWR | os.O_CREAT, 0o600)
        try:
            os.set_inheritable(fd, False)
            self._file = os.fdopen(fd, 'r+b', buffering=0)
        except BaseException:
            os.close(fd)
            raise
        try:
            if os.name == 'nt':
                import msvcrt
                msvcrt.locking(self._file.fileno(), msvcrt.LK_NBLCK, 1)
            elif os.name == 'posix':
                import fcntl
                fcntl.flock(self._file.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            else:
                raise RuntimeError('Unsupported OS for instance locking')
            self._check_identity()
        except BaseException as error:
            self._file.close()
            self._file = None
            raise RuntimeError('Cannot acquire database execution-service lock: ' + str(lock_path)) from error
        return self

    def __exit__(self, *exc):
        # Closing releases the kernel lock. Never unlink: waiters must use the same inode.
        if self._file is not None:
            self._file.close()
            self._file = None
