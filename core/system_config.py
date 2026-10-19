"""Shared atomic configuration access for UI and sequence workers."""
import json
import os
import shutil
import sys
import tempfile
import threading
import time

_locks = {}
_guard = threading.Lock()


def default_path():
    root = os.path.dirname(os.path.abspath(sys.executable)) if getattr(sys, 'frozen', False) else os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, 'config', 'sys_config.json')


def _lock(path):
    key = os.path.normcase(os.path.realpath(path))
    with _guard:
        return _locks.setdefault(key, threading.RLock())


def load(path=None):
    path = path or default_path()
    with _lock(path):
        try:
            with open(path, encoding='utf-8') as stream:
                data = json.load(stream)
        except FileNotFoundError:
            return {}
        if not isinstance(data, dict):
            raise ValueError('Invalid system configuration')
        return data


def update(data, path=None):
    path = os.path.abspath(path or default_path())
    with _lock(path):
        existing = load(path)
        existing.update(data)
        directory = os.path.dirname(path)
        os.makedirs(directory, exist_ok=True)
        fd, temporary = tempfile.mkstemp(dir=directory, suffix='.tmp')
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                json.dump(existing, stream, indent=4, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            if os.path.exists(path):
                shutil.copy2(path, path + '.bak')
            # Windows scanners can briefly hold the destination without delete sharing.
            # Keep the atomic replacement and bound retries; never truncate the old file.
            for attempt in range(4):
                try:
                    os.replace(temporary, path)
                    break
                except PermissionError as exc:
                    if getattr(exc, 'winerror', None) not in (5, 32, 33) or attempt == 3:
                        raise
                    time.sleep(.05 * (attempt + 1))
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
