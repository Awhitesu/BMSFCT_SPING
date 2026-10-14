import datetime
import os
import queue
import sys
import threading
import traceback


if getattr(sys, "frozen", False):
    _ROOT_DIR = os.path.dirname(os.path.abspath(sys.executable))
else:
    _ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_LOG_DIR = os.path.join(_ROOT_DIR, "logs")
_QUEUE = queue.Queue(maxsize=20000)
_LOCK = threading.Lock()
_STARTED = False
_DROPPED = 0
_SENTINEL = object()


def _clean(value, limit=500):
    try:
        text = str(value)
    except Exception:
        text = repr(value)
    text = text.replace("\r", "\\r").replace("\n", "\\n")
    if len(text) > limit:
        text = text[:limit] + "...<truncated>"
    return text


def _format_field(key, value):
    return f"{key}={_clean(value)!r}"


def _log_path():
    day = datetime.datetime.now().strftime("%Y%m%d")
    return os.path.join(_LOG_DIR, f"execution_trace_{day}.log")


def _writer():
    global _DROPPED
    os.makedirs(_LOG_DIR, exist_ok=True)
    current_path = None
    fp = None
    while True:
        item = _QUEUE.get()
        if item is _SENTINEL:
            break
        try:
            path = _log_path()
            if fp is None or path != current_path:
                if fp:
                    fp.close()
                current_path = path
                fp = open(current_path, "a", encoding="utf-8", buffering=1)
            dropped = 0
            with _LOCK:
                if _DROPPED:
                    dropped = _DROPPED
                    _DROPPED = 0
            if dropped:
                fp.write(f"{_timestamp()} [TRACE_DROPPED] count={dropped}\n")
            fp.write(item + "\n")
        except Exception:
            try:
                fallback = os.path.join(_LOG_DIR, "execution_trace_error.log")
                with open(fallback, "a", encoding="utf-8") as err:
                    err.write(traceback.format_exc() + "\n")
            except Exception:
                pass
    if fp:
        fp.close()


def _ensure_started():
    global _STARTED
    if _STARTED:
        return
    with _LOCK:
        if _STARTED:
            return
        t = threading.Thread(target=_writer, name="debug-trace-writer", daemon=True)
        t.start()
        _STARTED = True


def _timestamp():
    now = datetime.datetime.now()
    return now.strftime("[%Y-%m-%d %H:%M:%S.") + f"{now.microsecond // 1000:03d}]"


def trace(event, channel_id=None, **fields):
    """Write one non-blocking execution trace line to logs/execution_trace_YYYYMMDD.log."""
    global _DROPPED
    try:
        _ensure_started()
        thread = threading.current_thread()
        parts = [
            _timestamp(),
            f"[thread={thread.name}:{thread.ident}]",
        ]
        if channel_id is not None:
            try:
                parts.append(f"[CH-{int(channel_id):02d}]")
            except Exception:
                parts.append(f"[CH={_clean(channel_id)}]")
        parts.append(str(event))
        if fields:
            parts.extend(_format_field(k, v) for k, v in fields.items())
        _QUEUE.put_nowait(" ".join(parts))
    except queue.Full:
        with _LOCK:
            _DROPPED += 1
    except Exception:
        pass
