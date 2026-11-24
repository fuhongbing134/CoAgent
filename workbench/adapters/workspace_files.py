"""Stdio MCP file operations for a workbench-approved directory."""

import json
import ntpath
import os
from pathlib import Path, PureWindowsPath
import stat
import sys
import tempfile


def path_parts(name):
    windows = PureWindowsPath(name)
    if not windows.parts or windows.anchor:
        raise ValueError('Expected a relative workspace file path')
    for part in windows.parts:
        reserved = ntpath.isreserved(part) if hasattr(ntpath, 'isreserved') else PureWindowsPath(part).is_reserved()
        if reserved or part.casefold() in {'.git', '..'} or part.endswith(('.', ' ')) or ':' in part:
            raise ValueError('Expected a relative workspace file path')
    return windows.parts


def file_path(root, parts):
    path = Path(*parts)
    target = root
    for part in path.parts:
        target /= part
        try:
            info = target.lstat()
        except FileNotFoundError:
            if target != root / path:
                raise
            break
        if stat.S_ISLNK(info.st_mode) or (
            getattr(info, 'st_file_attributes', 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ):
            raise ValueError('Links and reparse points are not supported')
        if target == root / path and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
            raise ValueError('Expected a regular file with one link')
    if not target.resolve().is_relative_to(root):
        raise ValueError('Path is outside the workspace')
    return target


def operate(root, operation, arguments, diagnostic_path=None):
    if operation not in {'create_file', 'edit_file', 'delete_file'}:
        raise ValueError('Unknown file operation')
    parts = path_parts(arguments['path'])
    data = None if operation == 'delete_file' else arguments['text'].encode('utf-8')
    if os.name == 'nt':
        try:
            if __package__:
                from .workspace_files_windows import operate as operate_windows
            else:
                from workspace_files_windows import operate as operate_windows
        except OSError:
            record_stage(diagnostic_path, 'native_os_error')
            raise
        operate_windows(root, parts, operation, data)
        return f'{operation} succeeded'

    target = file_path(root, parts)
    if operation == 'delete_file':
        target.unlink()
    elif operation == 'create_file':
        with target.open('xb') as stream:
            stream.write(data)
    elif operation == 'edit_file':
        original = target.stat()
        with tempfile.NamedTemporaryFile(dir=target.parent, delete=False) as stream:
            staged = Path(stream.name)
            try:
                stream.write(data)
                stream.close()
                current = file_path(root, parts).stat()
                if (current.st_dev, current.st_ino, current.st_size, current.st_mtime_ns) != (
                    original.st_dev, original.st_ino, original.st_size, original.st_mtime_ns
                ):
                    raise ValueError('File changed before replacement')
                os.chmod(staged, stat.S_IMODE(original.st_mode))
                staged.replace(target)
            finally:
                staged.unlink(missing_ok=True)
    return f'{operation} succeeded'


def tools():
    for name, description in (
        ('create_file', 'Create a UTF-8 file without overwriting it.'),
        ('edit_file', 'Replace the full contents of an existing UTF-8 file.'),
        ('delete_file', 'Delete a single existing file.'),
    ):
        properties = {'path': {'type': 'string', 'description': 'Workspace-relative file path'}}
        if name != 'delete_file':
            properties['text'] = {'type': 'string'}
        yield {'name': name, 'description': description, 'inputSchema': {
            'type': 'object', 'properties': properties, 'required': list(properties),
            'additionalProperties': False,
        }}


def record_stage(path, stage):
    if path is not None:
        try:
            with path.open('a', encoding='ascii') as stream:
                stream.write(stage + '\n')
        except OSError:
            pass


def serve(root, diagnostic_path=None):
    record_stage(diagnostic_path, 'started')
    record_stage(diagnostic_path, json.dumps({
        'event': 'process_identity', 'pid': os.getpid(), 'executable': sys.executable,
    }))
    for line in sys.stdin:
        try:
            request = json.loads(line)
        except json.JSONDecodeError:
            write_error(-32700, 'Parse error')
            continue
        if (not isinstance(request, dict) or request.get('jsonrpc') != '2.0'
                or not isinstance(request.get('method'), str)):
            write_error(-32600, 'Invalid request')
            continue
        if 'id' not in request:
            continue
        response = {'jsonrpc': '2.0', 'id': request['id']}
        method = request.get('method')
        params = request.get('params', {})
        if method == 'initialize':
            response['result'] = {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}},
                                  'serverInfo': {'name': 'workbench_files', 'version': '1.0'}}
        elif method == 'ping':
            response['result'] = {}
        elif method == 'tools/list':
            response['result'] = {'tools': list(tools())}
        elif method == 'tools/call':
            try:
                message = operate(root, params['name'], params['arguments'], diagnostic_path)
                error = False
            except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
                message, error = str(exc), True
            response['result'] = {'content': [{'type': 'text', 'text': message}], 'isError': error}
        else:
            response['error'] = {'code': -32601, 'message': 'Method not found'}
        print(json.dumps(response), flush=True)
        if method == 'initialize':
            record_stage(diagnostic_path, 'initialize_response')
        elif method == 'tools/list':
            record_stage(diagnostic_path, 'tools_list_response')


def write_error(code, message):
    print(json.dumps({'jsonrpc': '2.0', 'id': None, 'error': {'code': code, 'message': message}}), flush=True)


if __name__ == '__main__':
    sys.stdin.reconfigure(encoding='utf-8')
    diagnostic_path = Path(sys.argv[2]) if len(sys.argv) == 3 else None

    def report_uncaught(error_type, error, traceback):
        if issubclass(error_type, ImportError):
            frame = traceback
            while frame is not None:
                if (frame.tb_frame.f_globals.get('__name__') or '').endswith('workspace_files_windows'):
                    record_stage(diagnostic_path, 'native_import_uncaught')
                    break
                frame = frame.tb_next
        sys.__excepthook__(error_type, error, traceback)

    if diagnostic_path is not None:
        sys.excepthook = report_uncaught
    serve(Path(sys.argv[1]).absolute(), diagnostic_path)
