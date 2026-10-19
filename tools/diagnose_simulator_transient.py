"""Unloaded, authorized 1/2.5/4.2 V transient diagnosis. Restores and turns OFF."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parent))
from test_simulator_hardware import Instrument


def run(ip, folder, cycles, focus=False, matched_current=False, trials=12):
    d = Instrument(ip, folder)
    baseline = {}
    rows = []
    cleanup = {}
    result = dict(ip=ip, cycles=rows, cleanup=cleanup)

    def require(ok):
        if not ok:
            raise RuntimeError(d.last_error or 'Command/readback failed')

    def follow(channels, target, duration):
        end = time.monotonic() + duration
        samples = []
        start = time.monotonic()
        while time.monotonic() < end:
            for ch in channels:
                samples.append(dict(t=time.monotonic()-start, channel=ch,
                    voltage=d.measure_voltage(ch), current_mA=d.measure_current(ch),
                    on=d.read_output_state(ch)))
            time.sleep(.01)
        return samples

    try:
        require(d.connect())
        result['identity'] = d.identity
        for ch in range(1,25):
            settings = d.read_source_settings(ch)
            voltage = d.read_set_voltage(ch)
            state = d.read_output_state(ch)
            if settings is None or voltage is None or state is None:
                raise RuntimeError('Cannot save initial settings')
            baseline[ch] = dict(settings, voltage=voltage, output=state)
        d.event('BASELINE', channels=baseline)
        require(d.output_controls(False))
        for ch in range(1,25):
            require(d.set_range(ch, 'AUTO'))
            require(d.set_current_limit(ch, .1))
        require(d.set_voltages(4.2))
        for cycle in range(cycles):
            # Exact first 100-cycle control pattern from the failing acceptance run.
            target = (1.,2.5,4.2)[cycle%3]
            ch = cycle%24+1
            mode = ('HIGH','LOW','AUTO')[(cycle//24)%3]
            limit = ((.0001,.0005,.001) if mode=='LOW' else (.01,.05,.1))[(cycle//24)%3]
            require(d.set_range(ch, mode))
            require(d.set_current_limit(ch, limit))
            require(d.set_voltages(target))
            started = time.monotonic()
            confirmed = d.output_controls(True)
            confirm_error = d.last_error
            elapsed = time.monotonic()-started
            values = d.read_voltage_snapshot(range(1,25))
            bad = [i+1 for i,(v,t,on) in enumerate(values)
                   if abs(v-target)>.05 or abs(t-target)>.001 or not on]
            row = dict(cycle=cycle, target=target, changed_channel=ch, range=mode,
                       current_A=limit, on_confirmed=confirmed, on_error=confirm_error,
                       on_seconds=elapsed, snapshot=values, bad_channels=bad)
            rows.append(row)
            if bad or not confirmed:
                # No writes until recovery data is captured, so a repeat command cannot hide it.
                watch = bad or [1,2,ch]
                row['followup'] = follow(list(dict.fromkeys(watch)), target, 2.)
                row['settings_after_followup'] = {c:d.read_source_settings(c) for c in watch}
                row['final_snapshot'] = d.read_voltage_snapshot(range(1,25))
                d.event('ANOMALY', **row)
                print(ip, 'ANOMALY',cycle,bad,flush=True)
            d.event('CYCLE', **{k:v for k,v in row.items() if k!='followup'})
            require(d.output_controls(False))
            time.sleep(.05)
            if (cycle+1)%20==0:
                print(ip, cycle+1, '/', cycles, 'cycles', flush=True)
        if focus:
            result['focused_cases'] = []
            watch_ch = 2 if ip.endswith('.112') else 1
            for ch in range(1,25):
                require(d.set_range(ch, 'HIGH'))
                require(d.set_current_limit(ch, .01))
            # Distinct channel setpoints detect response ordering/mapping errors.
            targets = {ch:(1.,2.5,4.2)[(ch-1)%3] for ch in range(1,25)}
            for ch,target in targets.items():require(d.set_voltage(ch,target))
            require(d.output_controls(True))
            time.sleep(.5)
            result['mapping_checks'] = []
            for order in [list(range(1,25)),list(range(24,0,-1))]*4:
                values = list(map(float,d._query_channels('MEAS{ch}:VOLT?',order)))
                result['mapping_checks'].append(dict(order=order,values=values,
                    bad=[ch for ch,v in zip(order,values) if abs(v-targets[ch])>.05]))
            require(d.output_controls(False))
            for trial in range(trials):
                for mode in ('HIGH','LOW'):
                    require(d.set_range(watch_ch,'HIGH'))
                    require(d.set_current_limit(watch_ch,.01))
                    require(d.set_voltages(1.))
                    require(d.output_controls(True))
                    require(d.output_controls(False))
                    require(d.set_range(watch_ch,mode))
                    require(d.set_current_limit(watch_ch,.0005 if mode=='LOW' or matched_current else .01))
                    target = 2.5 if ip.endswith('.112') else 4.2
                    require(d.set_voltages(target))
                    start = time.perf_counter()
                    # Read just one channel, no query pipeline: isolates batch association.
                    require(d._safe_send(('OUTP:ONOFF 1(@'+','.join(map(str,range(1,25)))+')\n').encode()))
                    samples = []
                    while time.perf_counter()-start<1.2:
                        t0 = time.perf_counter()-start
                        voltage = d.measure_voltage(watch_ch)
                        samples.append(dict(t=t0,voltage=voltage))
                        time.sleep(.005)
                    seen_good = False
                    regressions = []
                    for sample in samples:
                        good = abs(sample['voltage']-target)<=.05
                        if seen_good and not good:regressions.append(sample)
                        seen_good |= good
                    row = dict(trial=trial,mode=mode,channel=watch_ch,target=target,
                        current_limit_A=.0005 if mode=='LOW' or matched_current else .01,
                        samples=samples,regressions=regressions,
                        final_voltage=d.measure_voltage(watch_ch),
                        current_mA=d.measure_current(watch_ch),
                        on=d.read_output_state(watch_ch))
                    result['focused_cases'].append(row)
                    d.event('FOCUSED_CASE',**row)
                    require(d.output_controls(False))
                if (trial+1)%4==0:print(ip,'focused',trial+1,'/',trials,flush=True)
    except Exception as exc:
        result['error'] = str(exc)
        print(ip,'ERROR',exc,flush=True)
    finally:
        try:
            cleanup['off_before_restore'] = d.output_controls(False)
            if not cleanup['off_before_restore']:
                raise RuntimeError(d.last_error)
            cleanup['restore_errors'] = []
            for ch,settings in baseline.items():
                for name,fn in [('range',lambda:d.set_range(ch,settings['range'])),
                    ('limit',lambda:d.set_current_limit(ch,settings['current'])),
                    ('voltage',lambda:d.set_voltage(ch,settings['voltage']))]:
                    if not fn():cleanup['restore_errors'].append([ch,name,d.last_error])
            cleanup['final_off'] = d.output_controls(False)
            cleanup['states'] = [d.read_output_state(ch) for ch in range(1,25)]
            cleanup['all_off'] = all(x is False for x in cleanup['states'])
        except Exception as exc:
            cleanup['error'] = str(exc)
        d.disconnect()
        d.trace.close()
        result['anomalies'] = sum(bool(r['bad_channels']) or not r['on_confirmed'] for r in rows)
        result['focused_rebound_cases'] = sum(bool(r['regressions']) for r in result.get('focused_cases',[]))
        (folder/(ip+'_diagnosis.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--execute',action='store_true')
    p.add_argument('--output',required=True)
    p.add_argument('--cycles',type=int,default=100)
    p.add_argument('--focus',action='store_true')
    p.add_argument('--matched-current',action='store_true')
    p.add_argument('--trials',type=int,default=12)
    a=p.parse_args()
    if not a.execute or not 0<=a.cycles<=200 or (a.cycles==0 and not a.focus):
        p.error('Requires --execute, cycles 1..200 (or 0 with --focus)')
    if not 1<=a.trials<=12:p.error('trials must be 1..12')
    folder=Path(a.output)
    folder.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda ip:run(ip,folder,a.cycles,a.focus,a.matched_current,a.trials),
                              ['192.168.1.110','192.168.1.111','192.168.1.112']))
    (folder/'summary.json').write_text(json.dumps([
        {k:v for k,v in r.items() if k not in ('cycles','focused_cases','mapping_checks')} for r in results],indent=2),encoding='utf-8')
    print(json.dumps([{k:v for k,v in r.items() if k not in ('cycles','focused_cases','mapping_checks')} for r in results],indent=2),flush=True)
    sys.exit(0 if all(not r.get('error') and r['cleanup'].get('all_off') and
        not r['cleanup'].get('restore_errors') and not r['cleanup'].get('error') for r in results) else 2)
