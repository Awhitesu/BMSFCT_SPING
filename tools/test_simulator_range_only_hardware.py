"""已授权空载验证：每台仅CH24，区分准备、仅换量程及恢复阶段。"""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
import re
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from core.simulator_control import control_simulator
from tools.test_simulator_hardware import Instrument

IPS = ('192.168.1.110', '192.168.1.111', '192.168.1.112')
CHANNEL = 24
PAIRS = [('HIGH', 'LOW'), ('LOW', 'HIGH'), ('HIGH', 'AUTO'),
         ('AUTO', 'LOW'), ('LOW', 'AUTO'), ('AUTO', 'HIGH')]


class Probe(Instrument):
    def __init__(self, ip, folder):
        self.phase, self.commands = 'preflight', []
        super().__init__(ip, folder)

    def event(self, kind, **values):
        if kind == 'TX':
            self.commands.extend(line for line in values['data'].splitlines() if line and not line.endswith('?'))
        super().event(kind, phase=self.phase, **values)


def checked(sim, value):
    if value is not True:
        raise RuntimeError(sim.last_error or 'Device operation failed')


def sample(sim):
    state = sim._configuration_snapshot([CHANNEL])[CHANNEL]
    state['measured_voltage'] = float(sim._query(f'MEAS{CHANNEL}:VOLT?'))
    state['measured_current_mA'] = float(sim._query(f'MEAS{CHANNEL}:CURR?'))
    if not all(math.isfinite(state[key]) for key in ('voltage', 'current', 'measured_voltage', 'measured_current_mA')):
        raise RuntimeError('Non-finite measurement')
    return state


def exercise(sim, initial, folder):
    result = dict(ip=sim.ip, identity=sim.identity, channel=CHANNEL, cases=[], cleanup={})
    original = initial[CHANNEL]
    try:
        for enabled in (False, True):
            for source, target in PAIRS:
                sim.phase = 'setup'
                checked(sim, sim.output_controls(False, [CHANNEL]))
                checked(sim, sim.set_range(CHANNEL, source))
                checked(sim, sim.set_current_limit(CHANNEL, .001))
                if enabled:
                    checked(sim, sim.output_controls(True, [CHANNEL]))
                before = sample(sim)
                assert before['output'] is enabled and before['range'] == source
                assert abs(before['current'] - .001) <= 1e-8

                sim.phase = 'range_only'
                first_command = len(sim.commands)
                started = time.perf_counter()
                checked(sim, control_simulator(sim, 'range', [CHANNEL], target))
                action_ms = (time.perf_counter() - started) * 1000
                writes = sim.commands[first_command:]
                if not all(re.fullmatch(rf'SOUR{CHANNEL}:RANG [023]', line) for line in writes):
                    raise RuntimeError('Unexpected non-range write during range-only action')
                sim.phase = 'observe'
                samples = []
                while time.perf_counter() - started < 1.05:
                    current = sample(sim)
                    current['ms'] = round((time.perf_counter() - started) * 1000, 3)
                    samples.append(current)
                    if abs(current['measured_current_mA']) > 2 or current['measured_voltage'] > original['voltage'] + .2:
                        raise RuntimeError('Unexpected unloaded output; stopping this device test')
                    time.sleep(.02)
                row = dict(source=source, target=target, enabled=enabled, action_ms=round(action_ms, 3),
                           writes=writes, before=before, after=samples[-1], samples=samples,
                           range_confirmed=all(s['range'] == target for s in samples),
                           output_state_unchanged=all(s['output'] is enabled for s in samples),
                           current_limit_unchanged=all(abs(s['current']-before['current']) <= 1e-8 for s in samples),
                           voltage_setting_unchanged=all(abs(s['voltage']-before['voltage']) <= .001 for s in samples),
                           voltage_self_read_ok=all(abs(s['measured_voltage']-(original['voltage'] if enabled else 0)) <= .05 for s in samples),
                           min_voltage=min(s['measured_voltage'] for s in samples),
                           max_voltage=max(s['measured_voltage'] for s in samples))
                result['cases'].append(row)
                sim.event('RANGE_RESULT', **{key: value for key, value in row.items() if key != 'samples'})
                print(json.dumps(dict(ip=sim.ip, enabled=enabled, source=source, target=target,
                    range_ok=row['range_confirmed'], output_same=row['output_state_unchanged'],
                    limit_same=row['current_limit_unchanged'], voltage_ok=row['voltage_self_read_ok'],
                    min_V=row['min_voltage'], max_V=row['max_voltage']), ensure_ascii=False), flush=True)
    except Exception as exc:
        result['error'] = str(exc)
        sim.event('TEST_ERROR', error=str(exc))
    finally:
        sim.phase = 'restore'
        errors = []
        for name, operation in (
            ('off', lambda: sim.output_controls(False, [CHANNEL])),
            ('range', lambda: sim.set_range(CHANNEL, original['range'])),
            ('current', lambda: sim.set_current_limit(CHANNEL, original['current'])),
        ):
            try:
                checked(sim, operation())
            except Exception as exc:
                errors.append(name + ': ' + str(exc))
        try:
            voltage = sim.read_set_voltage(CHANNEL)
            if voltage is None:
                raise RuntimeError('Cannot confirm original voltage setting')
            if abs(voltage - original['voltage']) > .001:
                checked(sim, sim.set_voltage(CHANNEL, original['voltage']))
            final = sim._configuration_snapshot(list(range(1, 25)))
            result['cleanup']['final'] = final
            result['cleanup']['changed_channels'] = [ch for ch in initial if any(
                abs(final[ch][key] - initial[ch][key]) > 1e-8 if key in ('voltage', 'current')
                else final[ch][key] != initial[ch][key]
                for key in ('voltage', 'current', 'range', 'output'))]
            result['cleanup']['all_24_off'] = all(not state['output'] for state in final.values())
        except Exception as exc:
            errors.append('final_read: ' + str(exc))
        result['cleanup']['errors'] = errors
        (folder / (sim.ip + '_result.json')).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    return result


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true')
    parser.add_argument('--unloaded', action='store_true')
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    if not args.execute or not args.unloaded:
        parser.error('Explicit --execute --unloaded required')
    folder = Path(args.output)
    folder.mkdir(parents=True, exist_ok=True)
    devices = [Probe(ip, folder) for ip in IPS]
    initial, results = {}, []
    try:
        for sim in devices:
            checked(sim, sim.connect())
            initial[sim.ip] = sim._configuration_snapshot(list(range(1, 25)))
            if any(state['output'] for state in initial[sim.ip].values()):
                raise RuntimeError('Preflight requires all 72 outputs OFF')
            target = initial[sim.ip][CHANNEL]
            if not 0 < target['voltage'] <= 4.2 or abs(target['current']-.001) > 1e-8:
                raise RuntimeError('Preflight requires CH24 at existing <=4.2V and 1mA limit')
        (folder / 'preflight.json').write_text(json.dumps(initial, ensure_ascii=False, indent=2), encoding='utf-8')
        with ThreadPoolExecutor(max_workers=3) as pool:
            results = list(pool.map(lambda sim: exercise(sim, initial[sim.ip], folder), devices))
        (folder / 'summary.json').write_text(json.dumps(dict(time=datetime.now().astimezone().isoformat(),
              results=results), ensure_ascii=False, indent=2), encoding='utf-8')
    finally:
        for sim in devices:
            sim.disconnect()
            sim.trace.close()
    print(json.dumps([dict(ip=r['ip'], cases=len(r['cases']), error=r.get('error'),
         cleanup={key: value for key, value in r['cleanup'].items() if key != 'final'}) for r in results], ensure_ascii=False))


if __name__ == '__main__':
    main()
