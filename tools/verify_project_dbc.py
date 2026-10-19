"""Read-only real-project DBC verification; no physical CAN access."""
import argparse
import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.dbc_signals import import_dbc, database, catalog, DBCMonitor


class Driver:
    channel = 'PCAN_USBBUS1'
    is_connected = True
    def __init__(self):
        self.bus = object()
        self.listeners = set()
    def add_message_listener(self, callback): self.listeners.add(callback)
    def remove_message_listener(self, callback): self.listeners.discard(callback)
    def emit(self, message, payload):
        for callback in tuple(self.listeners):
            callback(dict(can_id=message.frame_id, data=payload,
                          interface=self.channel, direction='RX',
                          is_extended_id=message.is_extended_frame))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('dbc')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    asset = import_dbc(args.dbc)
    db = database(asset)
    driver = Driver()
    monitor = DBCMonitor()
    monitor.configure(asset, driver)
    results = []
    covered = set()
    try:
        for message in db.messages:
            muxes = [s for s in message.signals if s.is_multiplexer]
            assert len(muxes) == 1, 'This verifier requires one top-level multiplexer'
            mux = muxes[0]
            selectors = sorted({i for s in message.signals for i in (s.multiplexer_ids or [])})
            for selector in selectors:
                active = [s for s in message.signals if s.name == mux.name or selector in (s.multiplexer_ids or [])]
                for pattern in (0, 1):
                    raw = {s.name: pattern for s in active}
                    raw[mux.name] = selector
                    payload = message.encode(raw, scaling=False, strict=False)
                    expected = message.decode(payload, decode_choices=False)
                    for signal in active:
                        assert expected[signal.name] == raw[signal.name] * signal.scale + signal.offset
                    # Start a clean cache per case: no prior branch can satisfy the check.
                    monitor.configure(asset, driver, context_key=f'{message.name}/{selector}/{pattern}')
                    driver.emit(message, payload)
                    deadline = time.perf_counter() + 2
                    while len(monitor.snapshot()) < len(expected) and time.perf_counter() < deadline:
                        time.sleep(.002)
                    snapshot = monitor.snapshot()
                    for name, value in expected.items():
                        actual = snapshot.get((driver.channel, message.frame_id, message.is_extended_frame, name))
                        assert actual is not None, (message.name, selector, name, monitor.last_error)
                        assert actual['value'] == value, (name, actual, value)
                        covered.add((message.name, name))
                    results.append(dict(message=message.name, selector=selector,
                                        pattern=pattern, bytes=len(payload), signals=len(expected)))
    finally:
        monitor.stop()
    assert not driver.listeners
    assert len(covered) == len(catalog(asset)), (len(covered), len(catalog(asset)))
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    summary = dict(source=str(Path(args.dbc).resolve()), sha256=asset['sha256'],
                   messages=len(db.messages), signals=len(covered), cases=len(results),
                   passed=True, physical_hardware=False, results=results)
    output.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps({k:v for k,v in summary.items() if k != 'results'}, ensure_ascii=False))


if __name__ == '__main__': main()
