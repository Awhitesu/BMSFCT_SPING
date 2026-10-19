"""Authorized unloaded LOW-range voltage/current hold diagnosis."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.test_simulator_hardware import Instrument


def probe(ip,ch,folder):
    d=Instrument(ip,folder);cases=[];baseline=None;cleanup={}
    try:
        if not d.connect():raise RuntimeError(d.last_error)
        baseline=dict(d.read_source_settings(ch),voltage=d.read_set_voltage(ch))
        if not d.output_controls(False):raise RuntimeError(d.last_error)
        for limit in (.0001,.0005,.001):
            for target in (1.,2.5,4.2):
                if not d.output_control(ch,False):raise RuntimeError(d.last_error)
                for ok in (d.set_range(ch,'LOW'),d.set_current_limit(ch,limit),d.set_voltage(ch,target)):
                    if not ok:raise RuntimeError(d.last_error)
                started=time.perf_counter();enabled=d.output_control(ch,True)
                enable_ms=(time.perf_counter()-started)*1000
                deadline=time.perf_counter()+5;samples=[]
                while time.perf_counter()<deadline:
                    sample=dict(ms=round((time.perf_counter()-started)*1000,3),voltage=d.measure_voltage(ch),
                                current_mA=d.measure_current(ch),output=d.read_output_state(ch))
                    samples.append(sample);time.sleep(.05)
                r=dict(ip=ip,channel=ch,target_V=target,current_limit_mA=limit*1000,
                    enable_api_ok=enabled,enable_ms=round(enable_ms,3),samples=samples,
                    out_of_band=sum(abs(x['voltage']-target)>.05 for x in samples),
                    min_V=min(x['voltage'] for x in samples),max_V=max(x['voltage'] for x in samples),
                    min_mA=min(x['current_mA'] for x in samples),max_mA=max(x['current_mA'] for x in samples))
                cases.append(r);d.event('HOLD_RESULT',**{k:v for k,v in r.items() if k!='samples'})
                print(f'{ip} CH{ch} LOW {limit*1000:g}mA {target:g}V: {r["out_of_band"]}/{len(samples)} out of band',flush=True)
    except Exception as exc:cleanup['error']=str(exc)
    finally:
        try:
            cleanup['off']=d.output_controls(False)
            if baseline and cleanup['off']:
                cleanup['restore']=[d.set_range(ch,baseline['range']),d.set_current_limit(ch,baseline['current']),d.set_voltage(ch,baseline['voltage'])]
            cleanup['states']=[d.read_output_state(i) for i in range(1,25)]
            cleanup['all_off']=all(state is False for state in cleanup['states'])
        except Exception as exc:cleanup['error']=str(exc)
        d.disconnect();d.trace.close()
    result=dict(ip=ip,channel=ch,cases=cases,cleanup=cleanup)
    (folder/(ip+'_hold.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--output',required=True);a=p.parse_args()
    if not a.execute:p.error('--execute required')
    folder=Path(a.output);folder.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(probe,ip,ch,folder) for ip,ch in [('192.168.1.110',1),('192.168.1.111',1),('192.168.1.112',2)]]
        results=[f.result() for f in futures]
    (folder/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print('Hold diagnosis finished; see summary.json',flush=True)
