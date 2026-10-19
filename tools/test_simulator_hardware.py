"""Explicitly authorized, unloaded N83624 hardware acceptance test.

Writes only to the supplied three endpoints. Always attempts all-output OFF.
"""
import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
import json
import math
from pathlib import Path
import statistics
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from devices.ngi_83624 import NGI83624


class Instrument(NGI83624):
    def __init__(self,ip,folder):
        super().__init__(ip,7000,24)
        self.trace=(folder/(ip+'_scpi.jsonl')).open('w',encoding='utf-8')
    def event(self,kind,**values):
        self.trace.write(json.dumps(dict(time=datetime.now().astimezone().isoformat(),kind=kind,**values),ensure_ascii=False)+'\n')
        self.trace.flush()
    def _read_line(self):
        value=super()._read_line();self.event('RX',data=value);return value
    def _safe_send(self,cmd_bytes,*args,**kwargs):
        self.event('TX',data=cmd_bytes.decode('ascii').strip())
        return super()._safe_send(cmd_bytes,*args,**kwargs)
    def _verify_output_voltage(self,channels,logger=None,**kwargs):
        result=super()._verify_output_voltage(channels,logger,**kwargs)
        self.event('OUTPUT_CONFIRMATION',**self.last_output_confirmation)
        return result


def exercise(ip,folder,cycles):
    d=Instrument(ip,folder);rows=[];baseline={};cleanup={};started=time.perf_counter()
    def operation(name,action,check=lambda value:value is True,allow_failure=False,**parameters):
        t=time.perf_counter();value=None;error=''
        try:
            value=action()
            if not check(value):raise RuntimeError(d.last_error or f'Unexpected result: {value}')
        except Exception as exc:error=str(exc)
        row=dict(op=name,ms=round((time.perf_counter()-t)*1000,3),ok=not error,result=value,error=error,**parameters)
        rows.append(row);d.event('OPERATION',**row)
        if error and not allow_failure:raise RuntimeError(f'{name}: {error}')
        return value
    def measured(ch,target):
        deadline=time.perf_counter()+3
        while True:
            voltage=d.measure_voltage(ch)
            if math.isfinite(voltage) and abs(voltage-target)<=.05:return voltage
            if time.perf_counter()>=deadline:raise RuntimeError(f'CH{ch} target={target}, measured={voltage}')
            time.sleep(.01)
    def voltage_control(ch,target):
        if not d.set_voltage(ch,target):raise RuntimeError(d.last_error)
        return measured(ch,target)
    def snapshot(target):
        values=d.read_voltage_snapshot(range(1,25))
        if len(values)!=24 or any(not state or abs(v-target)>.05 or abs(setting-target)>.001 for v,setting,state in values):
            raise RuntimeError(f'Voltage snapshot mismatch: {values}')
        return dict(min_V=min(v for v,_,_ in values),max_V=max(v for v,_,_ in values),channels=24)
    try:
        operation('connect',d.connect)
        for ch in range(1,25):
            settings=d.read_source_settings(ch);voltage=d.read_set_voltage(ch);state=d.read_output_state(ch)
            if settings is None or voltage is None or state is None:raise RuntimeError(f'Cannot snapshot CH{ch}')
            baseline[ch]=dict(settings,voltage=voltage,output=state)
        d.event('BASELINE',channels=baseline)
        operation('all_output_off',lambda:d.output_controls(False))
        for mode in ('HIGH','LOW','AUTO'):
            for ch in range(1,25):
                operation('set_range',lambda ch=ch,mode=mode:d.set_range(ch,mode),channel=ch,range=mode)
                for limit in ((.0001,.0005,.001) if mode=='LOW' else (.01,.05,.1)):
                    operation('set_current_limit',lambda ch=ch,limit=limit:d.set_current_limit(ch,limit),allow_failure=mode=='LOW',channel=ch,current_A=limit,range=mode)
                    operation('read_current_limit',lambda ch=ch:d.read_source_settings(ch),
                              lambda value,limit=limit,mode=mode:value is not None and abs(value['current']-limit)<.000011 and value['range']==mode,allow_failure=mode=='LOW',channel=ch,range=mode)
        operation('batch_set_voltage',lambda:d.set_voltages(1.0),target_V=1.0)
        operation('all_output_on_measured',lambda:d.output_controls(True))
        for ch in range(1,25):
            for voltage in (1.0,2.5,4.2):
                operation('single_voltage_to_measured',lambda ch=ch,voltage=voltage:voltage_control(ch,voltage),
                          lambda value,voltage=voltage:abs(value-voltage)<=.05,channel=ch,target_V=voltage)
                operation('read_voltage',lambda ch=ch:d.measure_voltage(ch),
                          lambda value,voltage=voltage:math.isfinite(value) and abs(value-voltage)<=.05,channel=ch)
                operation('read_current_open_circuit',lambda ch=ch:d.measure_current(ch),
                          lambda value:math.isfinite(value) and abs(value)<1,channel=ch,screening_limit_mA=1)
        operation('all_output_off',lambda:d.output_controls(False))
        for cycle in range(cycles):
            target=(1.0,2.5,4.2)[cycle%3];ch=cycle%24+1
            mode=('HIGH','LOW','AUTO')[(cycle//24)%3]
            limit=((.0001,.0005,.001) if mode=='LOW' else (.01,.05,.1))[(cycle//24)%3]
            operation('set_range',lambda:d.set_range(ch,mode),channel=ch,range=mode,cycle=cycle)
            operation('set_current_limit',lambda:d.set_current_limit(ch,limit),channel=ch,current_A=limit,cycle=cycle)
            operation('batch_set_voltage',lambda:d.set_voltages(target),target_V=target,cycle=cycle)
            operation('all_output_on_measured',lambda:d.output_controls(True),cycle=cycle)
            operation('read_all_voltage_measured',lambda:snapshot(target),lambda value:value['channels']==24,cycle=cycle)
            operation('read_current_open_circuit',lambda:d.measure_current(ch),lambda value:math.isfinite(value) and abs(value)<1,channel=ch,cycle=cycle)
            operation('all_output_off',lambda:d.output_controls(False),cycle=cycle)
            if (cycle+1)%10==0:print(f'{ip}: {cycle+1}/{cycles} cycles, {len(rows)} operations, {sum(not r["ok"] for r in rows)} total failed checks',flush=True)
            time.sleep(.05)
    except Exception as exc:
        d.event('ABORT',error=str(exc));cleanup['test_error']=str(exc)
        print(f'{ip}: TEST FAILED: {exc}',flush=True)
    finally:
        try:
            cleanup['off_before_restore']=d.output_controls(False)
            if not cleanup['off_before_restore']:raise RuntimeError(d.last_error)
            restore_errors=[]
            for ch,settings in baseline.items():
                for name,fn in [('range',lambda:d.set_range(ch,settings['range'])),
                                ('limit',lambda:d.set_current_limit(ch,settings['current'])),
                                ('voltage',lambda:d.set_voltage(ch,settings['voltage']))]:
                    if not fn():restore_errors.append(f'CH{ch} {name}: {d.last_error}')
            cleanup['restore_errors']=restore_errors
            cleanup['final_off_command']=d.output_controls(False)
            cleanup['final_states']=[d.read_output_state(ch) for ch in range(1,25)]
            cleanup['all_outputs_confirmed_off']=all(state is False for state in cleanup['final_states'])
        except Exception as exc:cleanup['cleanup_error']=str(exc)
        d.disconnect();d.trace.close()
    groups={}
    for name in sorted({r['op'] for r in rows}):
        values=sorted(r['ms'] for r in rows if r['op']==name and r['ok'])
        groups[name]=dict(attempts=sum(r['op']==name for r in rows),failures=sum(r['op']==name and not r['ok'] for r in rows))
        if values:groups[name].update(mean_ms=round(statistics.mean(values),3),p95_ms=values[min(len(values)-1,math.ceil(len(values)*.95)-1)],max_ms=max(values))
    result=dict(ip=ip,identity=getattr(d,'identity',None),elapsed_s=round(time.perf_counter()-started,3),
        requested_cycles=cycles,completed_cycles=sum(r['op']=='read_all_voltage_measured' and r['ok'] for r in rows),
        operations=len(rows),failures=sum(not r['ok'] for r in rows),metrics=groups,cleanup=cleanup)
    (folder/(ip+'_summary.json')).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--cycles',type=int,default=100)
    p.add_argument('--output',required=True);args=p.parse_args()
    if not args.execute:p.error('Requires explicit --execute hardware authorization')
    if not 1<=args.cycles<=1000:p.error('cycles must be 1..1000')
    folder=Path(args.output);folder.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        futures=[pool.submit(exercise,ip,folder,args.cycles) for ip in ('192.168.1.110','192.168.1.111','192.168.1.112')]
        results=[f.result() for f in as_completed(futures)]
    (folder/'summary.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(results,ensure_ascii=False,indent=2),flush=True)
    sys.exit(0 if all(r['failures']==0 and r['completed_cycles']==args.cycles and
        r['cleanup'].get('all_outputs_confirmed_off') and not r['cleanup'].get('restore_errors') and
        not r['cleanup'].get('cleanup_error') and not r['cleanup'].get('test_error') for r in results) else 2)
