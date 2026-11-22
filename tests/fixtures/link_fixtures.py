"""Locate optional, host-prepared real links; their creator owns cleanup."""

import os
from pathlib import Path


def prepared_link_fixture(name):
    directory = os.environ.get('COAGENT_LINK_FIXTURES')
    if directory is None:
        return None
    root = Path(directory)
    if not root.is_absolute():
        raise ValueError('COAGENT_LINK_FIXTURES must be an absolute directory')
    fixture = root.resolve(strict=True) / name
    if not fixture.is_dir():
        raise FileNotFoundError(f'Missing prepared link fixture: {fixture}')
    return fixture
