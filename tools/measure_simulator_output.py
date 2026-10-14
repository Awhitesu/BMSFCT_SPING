"""Authorized live OFF/ON timing test; preserve configured voltage and current."""
import json
from pathlib import Path
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from devices.ngi_83624 import NGI83624
from devices.simulator_config import simulator_settings


def main():
    cfg = json.loads((ROOT / 'config/sys_config.json').read_text(encoding='utf-8'))
    units = [(setting, NGI83624(setting['ip'], setting['port']))
             for setting in simulator_settings(cfg) if setting['enabled']]
    report = {'started': datetime.now().isoformat(), 'devices': []}
    def prepare(unit):
        setting, sim = unit
        channels = list(range(1, setting['channels'] + 1))
        if not sim.connect():
            raise RuntimeError(f"Cannot connect {setting['ip']}:{setting['port']}")
        snapshot = sim.read_voltage_snapshot(channels)
        return {'number': setting['number'], 'ip': setting['ip'],
                'channels': channels, 'initial': snapshot}

    def timed_output(unit, state):
        setting, sim = unit
        start = time.perf_counter()
        ok = sim.output_controls(state, range(1, setting['channels'] + 1))
        return {'ok': ok, 'seconds': round(time.perf_counter() - start, 4),
                'error': sim.last_error}

    def voltage_ready(unit):
        setting, sim = unit
        record = next(r for r in report['devices'] if r['number'] == setting['number'])
        channels = record['channels']
        pending = dict(zip(channels, [row[1] for row in record['initial']]))
        confirmed = {}
        start = time.perf_counter()
        while pending and time.perf_counter() - start < 3:
            local = list(pending)
            values = sim._query_channels('MEAS{ch}:VOLT?', local)
            for ch, value in zip(local, values):
                measured = float(value)
                if abs(measured - pending[ch]) <= .05:
                    confirmed[ch] = {'voltage': measured,
                                     'seconds_after_on_command': round(time.perf_counter() - on_start, 4)}
                    del pending[ch]
            if pending:
                time.sleep(.1)
        return {'confirmed': confirmed, 'unconfirmed': pending,
                'note': 'Zero setpoints cannot prove nonzero voltage output.'}

    try:
        with ThreadPoolExecutor(max_workers=max(1, len(units))) as pool:
            # Do not change any outputs if preflight fails on any device.
            report['devices'] = list(pool.map(prepare, units))
            started = time.perf_counter()
            off_results = list(pool.map(lambda u: timed_output(u, False), units))
            report['all_off_seconds'] = round(time.perf_counter() - started, 4)
            for record, result in zip(report['devices'], off_results):
                record['off'] = result
            # Always attempt ON for every prepared device, including OFF failures.
            on_start = time.perf_counter()
            on_results = list(pool.map(lambda u: timed_output(u, True), units))
            report['all_on_state_seconds'] = round(time.perf_counter() - on_start, 4)
            for record, result in zip(report['devices'], on_results):
                record['on'] = result
            readings = list(pool.map(voltage_ready, units))
            for record, result in zip(report['devices'], readings):
                record['voltage_check'] = result
    except Exception as exc:
        report['error'] = str(exc)
    finally:
        for _, sim in units:
            sim.disconnect()
        path = ROOT / 'logs' / f'simulator_off_on_{datetime.now():%Y%m%d_%H%M%S}.json'
        path.parent.mkdir(exist_ok=True)
        path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
        print(json.dumps(report, ensure_ascii=False, indent=2))
        print(f'Report: {path}')


if __name__ == '__main__':
    main()
