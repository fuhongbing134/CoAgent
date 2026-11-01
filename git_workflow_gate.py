"""Process-local ordering of stop acceptance and host Git process starts.

Never hold this lock while waiting for a child process or an Agent invocation.
The workbench's existing singleton service owns this gate, not the Agent shells.
"""
import threading

GIT_START_GATE = threading.RLock()
