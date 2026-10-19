"""Authorized open-lead DC/AC voltage and range test. Restores saved settings."""
import argparse
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import math
from pathlib import Path
import sys
import time
from types import SimpleNamespace

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from devices.keysight_34461a import Keysight34461A
from core.device_steps import encode,execute


class Instrument(Keysight34461A):
    def __init__(self,ip,folder):
        super().__init__(ip)
        self.trace=(folder/'scpi.jsonl').open('w',encoding='utf-8')
    def event(self,kind,**values):
        self.trace.write(json.dumps(dict(time=datetime.now().astimezone().isoformat(),kind=kind,**values),ensure_ascii=False)+'\n')
        self.trace.flush()
    def command(self,text):
        started=time.perf_counter()
        try:
            result=super().command(text)
            self.event('SCPI',command=text,result=result,ms=(time.perf_counter()-started)*1000)
            return result
        except Exception as exc:
            self.event('SCPI_ERROR',command=text,error=str(exc),ms=(time.perf_counter()-started)*1000)
            raise


SETTINGS=('VOLT:DC:RANG','VOLT:DC:RANG:AUTO','VOLT:DC:NPLC','VOLT:DC:ZERO:AUTO',
          'VOLT:DC:IMP:AUTO','VOLT:AC:RANG','VOLT:AC:RANG:AUTO','VOLT:AC:BAND',
          'TRIG:SOUR','TRIG:COUN','TRIG:DEL','TRIG:DEL:AUTO','SAMP:COUN')


def run(ip,folder):
    d=Instrument(ip,folder);baseline={};rows=[];cleanup={}
    def op(name,action,check=lambda x:True,allow_overrange=False,**parameters):
        start=time.perf_counter();value=None;error=''
        try:
            value=action()
            if not check(value):raise RuntimeError(f'Unexpected result: {value}')
        except Exception as exc:error=str(exc)
        overrange=bool(error) and getattr(d,'last_measurement',{}).get('status')=='overrange' and name=='driver_voltage_measure'
        row=dict(name=name,ok=not error,result=value,error=error,overrange=overrange,ms=(time.perf_counter()-start)*1000,**parameters)
        rows.append(row);d.event('CASE',**row)
        if error and not (allow_overrange and overrange):raise RuntimeError(name+': '+error)
        return value
    def errors():
        records=[]
        for _ in range(32):
            value=d.query('SYST:ERR?')
            if int(value.split(',',1)[0])==0:return records
            records.append(value)
        raise RuntimeError('Instrument error queue did not drain')
    def check_config(kind,requested):
        conf=d.query('CONF?')
        if kind=='VOLT:DC':ok=conf.startswith('"VOLT ') or conf.startswith('"VOLT:DC ')
        else:ok=conf.startswith('"VOLT:AC ')
        auto=d.query(kind+':RANG:AUTO?')
        actual=float(d.query(kind+':RANG?'))
        ok=ok and ((auto=='1') if requested=='AUTO' else (auto=='0' and math.isclose(actual,float(requested),rel_tol=1e-6)))
        return dict(ok=ok,configuration=conf,autorange=auto,range_V=actual)
    def valid(value):
        return len(value)==2 and math.isfinite(float(value[0])) and abs(value[0])<9e37 and value[1]=='V'
    manager=SimpleNamespace(dmm34461=d,is_virtual=False,is_enabled=lambda _:True,
        device_step_lock=lambda _:d._lock)
    functions=[(label,code) for label,(code,unit) in d.FUNCTIONS.items() if code in ('VOLT:DC','VOLT:AC')]
    try:
        op('connect',d.connect,lambda x:x is True)
        baseline['CONF']=d.query('CONF?')
        if not baseline['CONF'].startswith('"VOLT '):raise RuntimeError('Initial DC voltage configuration changed; stopped')
        for command in SETTINGS:baseline[command]=d.query(command+'?')
        baseline['historical_errors']=errors()
        (folder/'baseline.json').write_text(json.dumps(baseline,indent=2),encoding='utf-8')
        for label,code in functions:
            ranges=('AUTO',.1,1,10,100,1000) if code=='VOLT:DC' else ('AUTO',.1,1,10,100,1000)
            for requested in ranges:
                for sample in range(8):
                    op('driver_voltage_measure',lambda:d.measure(label,requested),valid,
                       function=code,requested_range=requested,sample=sample,allow_overrange=True)
                    if sample in (0,7):
                        op('configuration_readback',lambda:check_config(code,requested),lambda x:x['ok'],
                           function=code,requested_range=requested)
                op('instrument_error_check',errors,lambda x:not x,function=code,requested_range=requested)
                print(code,'range',requested,'completed; overrange samples',sum(r.get('overrange',False) for r in rows if r.get('function')==code and r.get('requested_range')==requested),flush=True)
            for sample in range(10):
                def typed():
                    ok,value=execute(manager,encode('dmm34461','measure',{'function':label,'range':'10','variable':'DMM_VALUE'}),{},lambda text:d.event('STEP_LOG',text=text))
                    return (float(value),value.unit) if ok else (None,None)
                op('typed_measurement_step',typed,valid,function=code,sample=sample)
        # Alternating functions across threads exercises the actual measure lock/response association.
        def worker(pair):
            label,code=pair
            result=[]
            for sample in range(10):
                t=time.perf_counter();reading=d.measure(label,'10')
                if not valid(reading):raise RuntimeError(str(reading))
                result.append(dict(function=code,value=reading[0],ms=(time.perf_counter()-t)*1000))
            return result
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(worker,functions))
        op('concurrent_measurements',lambda:results,lambda x:len(x)==2 and all(len(v)==10 for v in x))
        op('final_instrument_errors',errors,lambda x:not x)
        for cycle in range(10):op('reconnect',d.connect,lambda x:x is True,cycle=cycle)
    except Exception as exc:
        cleanup['test_error']=str(exc);print('FAILED:',exc,flush=True)
    finally:
        try:
            if baseline:
                if not d.is_connected and not d.connect():raise RuntimeError(d.last_error)
                if 'CONF' in baseline:d.command('CONF:'+baseline['CONF'].strip('"'))
                for command in SETTINGS:
                    if command in baseline:d.command(command+' '+baseline[command])
                cleanup['configuration']=d.query('CONF?')
                cleanup['settings']={command:d.query(command+'?') for command in SETTINGS if command in baseline}
                cleanup['mismatches']={command:[baseline[command],actual] for command,actual in cleanup['settings'].items()
                    if baseline[command]!=actual}
                cleanup['errors']=errors()
                cleanup['restored']=not cleanup['mismatches'] and not cleanup['errors'] and cleanup['configuration']==baseline['CONF']
        except Exception as exc:cleanup['cleanup_error']=str(exc)
        d.disconnect();d.trace.close()
    result=dict(ip=ip,identity=d.identity,checks=len(rows),failures=sum(not r['ok'] for r in rows),overrange_samples=sum(r.get('overrange',False) for r in rows),cleanup=cleanup,rows=rows)
    (folder/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2),flush=True)
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--ip',default='192.168.1.107');p.add_argument('--output',required=True);a=p.parse_args()
    if not a.execute:p.error('--execute required for voltage/range changes')
    folder=Path(a.output);folder.mkdir(parents=True,exist_ok=False)
    r=run(a.ip,folder)
    sys.exit(0 if not r['failures'] and not r['cleanup'].get('test_error') and r['cleanup'].get('restored') else 2)
