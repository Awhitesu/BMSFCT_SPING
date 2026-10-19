"""Final IP isolation and 24-channel LOW hold checks, then all-output OFF."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.test_simulator_hardware import Instrument


def main(folder):
    drivers=[];baselines={};isolation={};results=[]
    try:
        for ip in ('192.168.1.110','192.168.1.111','192.168.1.112'):
            d=Instrument(ip,folder);drivers.append(d)
            if not d.connect():raise RuntimeError(d.last_error)
            baselines[ip]={ch:dict(d.read_source_settings(ch),voltage=d.read_set_voltage(ch)) for ch in range(1,25)}
            if not d.output_controls(False):raise RuntimeError(d.last_error)
        for d,target in zip(drivers,(1.,2.5,4.2)):
            if not (d.set_range(1,'HIGH') and d.set_current_limit(1,.01) and d.set_voltage(1,target)):raise RuntimeError(d.last_error)
        for d,target in zip(drivers,(1.,2.5,4.2)):
            source=d.read_set_voltage(1);enabled=d.output_control(1,True);voltage=d.measure_voltage(1)
            isolation[d.ip]=dict(target=target,source=source,voltage=voltage,ok=enabled and abs(source-target)<.001 and abs(voltage-target)<=.05)
        for d in drivers:
            if not d.output_controls(False):raise RuntimeError(d.last_error)
        def hold(d):
            record=dict(ip=d.ip,snapshots=[],errors=[])
            try:
                for ch in range(1,25):
                    if not d.set_range(ch,'LOW') or not d.set_current_limit(ch,.0005):raise RuntimeError(d.last_error)
                if not d.set_voltages(4.2) or not d.output_controls(True):raise RuntimeError(d.last_error)
                for i in range(12):
                    voltages=d.read_voltage_snapshot(range(1,25))
                    with d._lock:currents=[float(x) for x in d._query_channels('MEAS{ch}:CURR?',range(1,25))]
                    record['snapshots'].append(dict(voltages=voltages,currents_mA=currents))
                    for ch,(v,setting,state) in enumerate(voltages,1):
                        if abs(v-4.2)>.05 or abs(setting-4.2)>.001 or not state:record['errors'].append(dict(snapshot=i,channel=ch,voltage=v,current_mA=currents[ch-1]))
                    time.sleep(.05)
            except Exception as exc:record['error']=str(exc)
            return record
        with ThreadPoolExecutor(max_workers=3) as pool:results=list(pool.map(hold,drivers))
    finally:
        for d in drivers:
            cleanup={}
            try:
                cleanup['off_before_restore']=d.output_controls(False)
                restored=[]
                if cleanup['off_before_restore']:
                    for ch,settings in baselines.get(d.ip,{}).items():
                        restored.extend((d.set_range(ch,settings['range']),d.set_current_limit(ch,settings['current']),d.set_voltage(ch,settings['voltage'])))
                cleanup['restore_ok']=bool(restored) and all(restored)
                cleanup['states']=[d.read_output_state(ch) for ch in range(1,25)]
                cleanup['all_off']=all(value is False for value in cleanup['states'])
            except Exception as exc:cleanup['error']=str(exc)
            d.event('FINAL_CLEANUP',**cleanup)
            row=next((r for r in results if r['ip']==d.ip),None)
            if row is not None:row['cleanup']=cleanup
            d.disconnect();d.trace.close()
    report=dict(isolation=isolation,low_hold=results)
    (folder/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(dict(isolation=isolation,low_hold=[dict(ip=r['ip'],snapshots=len(r['snapshots']),errors=r['errors'],cleanup=r.get('cleanup')) for r in results]),ensure_ascii=False))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--execute',action='store_true');parser.add_argument('--output',required=True);args=parser.parse_args()
    if not args.execute:parser.error('--execute required')
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=False);main(folder)
