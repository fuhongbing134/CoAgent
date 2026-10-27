"""Resolved session identity for a backend whose storage rules were independently verified.

No default roots/profile isolation are guessed here. Production activation requires
an evidence-backed resolver; this module neither loads credentials nor enables it.
"""
from dataclasses import dataclass, field
import hashlib
import json
import os
from pathlib import Path

from execution_tracking import SessionIdentity


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     allow_nan=False).encode('utf-8')).hexdigest()


@dataclass(frozen=True)
class ResolvedSessionConfiguration:
    identity: SessionIdentity
    evidence_reference: str
    executable: str
    profile: str | None = None
    patch_path: str | None = None
    environment: tuple = field(default=(), repr=False)
    tools_disabled: bool = False
    patch_digest: str | None = field(default=None, repr=False)

    @classmethod
    def verified(cls, *, adapter_id, storage_root, workspace, executable,
                 effective_config, evidence_reference, profile=None,
                 profile_isolates_storage=False, patch_path=None, environment=(),
                 tools_disabled=False):
        if not evidence_reference or adapter_id not in {'codex', 'dsh'}:
            raise ValueError('Backend storage evidence is required')
        # Explicit whitelist: credentials and raw configuration cannot enter snapshots.
        allowed = {'model', 'provider_id', 'endpoint_identity', 'recovery_format', 'sandbox'}
        if set(effective_config) - allowed:
            raise ValueError('Non-whitelisted recovery configuration')
        root = os.path.normcase(str(Path(storage_root).expanduser().resolve(strict=True)))
        directory = os.path.normcase(str(Path(workspace).resolve(strict=True)))
        if not Path(root).is_dir() or not Path(directory).is_dir():
            raise ValueError('Storage root and workspace must be existing directories')
        scope = adapter_id + ':' + digest([root, profile if profile_isolates_storage else None])
        identity = SessionIdentity(adapter_id, scope, digest([effective_config, profile]), directory, 0)
        identity.values()
        return cls(identity, evidence_reference, str(Path(executable).resolve()), profile,
                   str(Path(patch_path).resolve()) if patch_path else None,
                   tuple(environment), tools_disabled,
                   hashlib.sha256(Path(patch_path).read_bytes()).hexdigest() if patch_path else None)

    def check_launch(self, command, cwd, environment):
        if os.path.normcase(str(Path(cwd).resolve())) != self.identity.workspace_id:
            raise RuntimeError('Workspace changed after session resolution')
        if os.path.normcase(str(Path(command[0]).resolve())) != os.path.normcase(self.executable):
            raise RuntimeError('Executable changed after session resolution')
        if self.profile is not None:
            if '--profile' not in command or command[command.index('--profile') + 1] != self.profile:
                raise RuntimeError('Profile changed after session resolution')
        if self.patch_path is not None:
            if '--patch' not in command or str(Path(command[command.index('--patch') + 1]).resolve()) != self.patch_path:
                raise RuntimeError('Patch changed after session resolution')
            if hashlib.sha256(Path(self.patch_path).read_bytes()).hexdigest() != self.patch_digest:
                raise RuntimeError('Patch content changed after session resolution')
        for key, value in self.environment:
            if environment.get(key) != value:
                raise RuntimeError('Backend environment changed after session resolution')
