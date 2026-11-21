"""Windows workspace operations relative to open directory handles."""

from contextlib import ExitStack, contextmanager
import ctypes as C
from ctypes import wintypes as W
import msvcrt
import os
from pathlib import Path


class UnicodeString(C.Structure):
    _fields_ = [('length', W.USHORT), ('capacity', W.USHORT), ('buffer', W.LPWSTR)]


class ObjectAttributes(C.Structure):
    _fields_ = [
        ('length', W.ULONG), ('root', W.HANDLE), ('name', C.POINTER(UnicodeString)),
        ('attributes', W.ULONG), ('security', C.c_void_p), ('quality', C.c_void_p),
    ]


class IoStatus(C.Structure):
    _fields_ = [('status', C.c_void_p), ('information', C.c_size_t)]


class AttributeTag(C.Structure):
    _fields_ = [('attributes', W.DWORD), ('tag', W.DWORD)]


kernel = C.WinDLL('kernel32', use_last_error=True)
ntdll = C.WinDLL('ntdll')
kernel.CreateFileW.argtypes = [W.LPCWSTR, W.DWORD, W.DWORD, C.c_void_p, W.DWORD, W.DWORD, W.HANDLE]
kernel.CreateFileW.restype = W.HANDLE
kernel.CloseHandle.argtypes = [W.HANDLE]
kernel.DuplicateHandle.argtypes = [
    W.HANDLE, W.HANDLE, W.HANDLE, C.POINTER(W.HANDLE), W.DWORD, W.BOOL, W.DWORD,
]
kernel.GetFileInformationByHandleEx.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
kernel.SetFileInformationByHandle.argtypes = [W.HANDLE, C.c_int, C.c_void_p, W.DWORD]
kernel.GetFinalPathNameByHandleW.argtypes = [W.HANDLE, W.LPWSTR, W.DWORD, W.DWORD]
kernel.GetFinalPathNameByHandleW.restype = W.DWORD
ntdll.NtCreateFile.argtypes = [
    C.POINTER(W.HANDLE), W.ULONG, C.POINTER(ObjectAttributes), C.POINTER(IoStatus),
    C.c_void_p, W.ULONG, W.ULONG, W.ULONG, W.ULONG, C.c_void_p, W.ULONG,
]
ntdll.NtCreateFile.restype = W.LONG
ntdll.RtlNtStatusToDosError.argtypes = [W.LONG]
ntdll.RtlNtStatusToDosError.restype = W.ULONG

READ_ATTRIBUTES = 0x80
SYNCHRONIZE = 0x100000
DELETE = 0x10000
GENERIC_WRITE = 0x40000000
SHARE_READ = 1
SHARE_WRITE = 2
FILE_OPEN = 1
FILE_CREATE = 2
DIRECTORY_FILE = 1
NON_DIRECTORY_FILE = 0x40
OPEN_REPARSE_POINT = 0x200000
SYNCHRONOUS_IO = 0x20
REPARSE_ATTRIBUTE = 0x400
DIRECTORY_ATTRIBUTE = 0x10


@contextmanager
def open_child(parent, name, access, share, disposition, kind):
    size = len(name.encode('utf-16-le'))
    word = UnicodeString(size, size + 2, name)
    # Resolve a single name from the held parent without traversing reparse points.
    attributes = ObjectAttributes(C.sizeof(ObjectAttributes), parent, C.pointer(word), 0x40)
    handle = W.HANDLE()
    status = ntdll.NtCreateFile(
        C.byref(handle), access | SYNCHRONIZE, C.byref(attributes), C.byref(IoStatus()),
        None, 0, share, disposition, kind | OPEN_REPARSE_POINT | SYNCHRONOUS_IO, None, 0,
    )
    if status < 0:
        raise C.WinError(ntdll.RtlNtStatusToDosError(status))
    try:
        yield handle
    finally:
        kernel.CloseHandle(handle)


def check_handle(handle, directory=False):
    info = AttributeTag()
    if not kernel.GetFileInformationByHandleEx(handle, 9, C.byref(info), C.sizeof(info)):
        raise C.WinError(C.get_last_error())
    if info.attributes & REPARSE_ATTRIBUTE or bool(info.attributes & DIRECTORY_ATTRIBUTE) != directory:
        raise ValueError('Expected an ordinary directory or file without reparse points')
    # Check the normalized name too, since Windows can open entries by short aliases.
    name = C.create_unicode_buffer(32768)
    length = kernel.GetFinalPathNameByHandleW(handle, name, len(name), 0)
    if not length or length >= len(name):
        raise OSError('Could not read the normalized workspace path')
    if name.value.rsplit('\\', 1)[-1].casefold() == '.git':
        raise ValueError('Git internal paths are not supported')
    return Path(name.value.removeprefix('\\\\?\\'))


def mark_for_deletion(handle):
    delete = W.BOOLEAN(True)
    if not kernel.SetFileInformationByHandle(handle, 4, C.byref(delete), C.sizeof(delete)):
        raise C.WinError(C.get_last_error())


def operate(root, parts, operation, data):
    if len(root.drive) != 2 or root.drive[1] != ':':
        raise ValueError('Expected a local drive workspace')
    with ExitStack() as handles:
        # Open the approved root from the launcher, then keep each directory below it
        # open without FILE_SHARE_DELETE while doing handle-relative operations.
        parent = kernel.CreateFileW(str(root), READ_ATTRIBUTES | 0x20, SHARE_READ | SHARE_WRITE,
                                    None, 3, 0x02000000 | OPEN_REPARSE_POINT, None)
        if parent == C.c_void_p(-1).value:
            raise C.WinError(C.get_last_error())
        handles.callback(kernel.CloseHandle, parent)
        if check_handle(parent, directory=True) != root:
            raise ValueError('The opened directory does not match the approved workspace')
        for part in parts[:-1]:
            parent = handles.enter_context(open_child(
                parent, part, READ_ATTRIBUTES | 0x20, SHARE_READ | SHARE_WRITE, FILE_OPEN, DIRECTORY_FILE,
            ))
            check_handle(parent, directory=True)

        create = operation == 'create_file'
        deleting = operation == 'delete_file'
        access = READ_ATTRIBUTES | (DELETE if deleting else GENERIC_WRITE)
        if create:
            access |= DELETE
        handle = handles.enter_context(open_child(
            parent, parts[-1], access, SHARE_READ, FILE_CREATE if create else FILE_OPEN, NON_DIRECTORY_FILE,
        ))
        try:
            check_handle(handle)
            # Duplicate the handle so the Python stream and the native context have separate ownership.
            process = W.HANDLE(-1)
            duplicate = W.HANDLE()
            if not kernel.DuplicateHandle(process, handle, process, C.byref(duplicate), 0, 0, 2):
                raise C.WinError(C.get_last_error())
            try:
                fd = msvcrt.open_osfhandle(duplicate.value, os.O_BINARY | (os.O_RDONLY if deleting else os.O_WRONLY))
            except OSError:
                kernel.CloseHandle(duplicate)
                raise
            with os.fdopen(fd, 'rb' if deleting else 'wb') as stream:
                if os.fstat(stream.fileno()).st_nlink != 1:
                    raise ValueError('Expected a regular file with one link')
                if deleting:
                    mark_for_deletion(handle)
                else:
                    stream.write(data)
                    stream.truncate()
        except (OSError, ValueError):
            if create:
                mark_for_deletion(handle)
            raise
