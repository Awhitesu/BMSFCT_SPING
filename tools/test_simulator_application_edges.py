"""Authorized OFF/read/reconnect/cancellation/concurrency hardware checks."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import threading
from types import SimpleNamespace
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.test_simulator_hardware import Instrument
from core.simulator_control import control_simulator, read_simulator
from core.device_steps import encode, execute


def run(ip,folder,read_matrix=False,legacy_only=False):
    d=Instrument(ip,folder);baseline={};rows=[];cleanup={}
    manager=SimpleNamespace(is_virtual=False,_get_sim_and_ch=lambda ch:(d,ch))
    def typed_read(ch,quantity):
        ok,value=execute(manager,encode('simulator',quantity,{'channel':ch}),{},lambda text:d.event('STEP_LOG',text=text))
        if not ok:raise RuntimeError(str(value))
        return float(value)
    def checked(ok):
        if ok is not True:raise RuntimeError(d.last_error or 'Not confirmed')
    def op(name,action,check=lambda x:x is True,**params):
        start=time.perf_counter();result=None;error=''
        try:
            result=action()
            if not check(result):raise RuntimeError(str(result))
        except Exception as exc:error=str(exc)
        row=dict(name=name,ok=not error,result=result,error=error,
                 ms=(time.perf_counter()-start)*1000,**params)
        rows.append(row);d.event('EDGE',**row)
        if error:raise RuntimeError(name+': '+error)
        return result
    try:
        checked(d.connect())
        for ch in range(1,25):
            settings=d.read_source_settings(ch);voltage=d.read_set_voltage(ch)
            if settings is None or voltage is None:raise RuntimeError('Cannot save baseline')
            baseline[ch]=dict(settings,voltage=voltage)
        checked(d.output_controls(False))
        if legacy_only:
            from core.engine import ChannelWorker
            from devices.manager import DeviceManager
            real_manager=DeviceManager.__new__(DeviceManager)
            real_manager._get_sim_and_ch=lambda ch:(d,ch)
            worker=SimpleNamespace(channel_id=1,is_running=True)
            checked(control_simulator(d,'range',[1],'HIGH'))
            checked(control_simulator(d,'current',[1],.0005))
            checked(control_simulator(d,'range',[1],'LOW'))
            checked(control_simulator(d,'voltage_on',[1],2.5))
            def read(kind):
                return ChannelWorker._read_instrument_once(worker,real_manager,
                    {'device':'simulator','params':kind+' / CH:1'},lambda text:d.event('ENGINE_LOG',text=text))
            op('legacy_engine_voltage_read',lambda:read('VOLTAGE'),
               lambda v:v[0] and abs(v[1]-2.5)<=.05 and v[1].unit=='V')
            op('legacy_engine_current_read',lambda:read('CURRENT'),
               lambda v:v[0] and abs(v[1])<1 and v[1].unit=='mA')
            worker.is_running=False
            def cancelled_read():
                try:read('CURRENT')
                except RuntimeError:return d.read_output_state(1) is True
                return False
            op('legacy_engine_cancelled_read',cancelled_read)
            for mode in ('HIGH','LOW'):
                checked(control_simulator(d,'current',[1],.0005))
                checked(control_simulator(d,'range',[1],mode))
                for limit in ((.01,.05,.1) if mode=='HIGH' else (.0001,.0005,.001)):
                    op('precise_limit_readback',lambda:control_simulator(d,'current',[1],limit),
                       range=mode,current_A=limit)
        elif read_matrix:
            allch=list(range(1,25))
            checked(control_simulator(d,'range',allch,'HIGH',batch=True))
            for mode in ('HIGH','AUTO','LOW'):
                checked(control_simulator(d,'current',allch,.0005,batch=True))
                op('matrix_range',lambda:control_simulator(d,'range',allch,mode,batch=True),range=mode)
                limits=(.0001,.0005,.001) if mode=='LOW' else (.01,.05,.1)
                for target,limit in zip((1.,2.5,4.2),limits):
                    op('matrix_current',lambda:control_simulator(d,'current',allch,limit,batch=True),range=mode,current_A=limit)
                    op('matrix_voltage_on',lambda:control_simulator(d,'voltage_on',allch,target,batch=True),range=mode,target_V=target)
                    for ch in allch:
                        op('matrix_voltage_read',lambda:typed_read(ch,'voltage'),lambda v:abs(v-target)<=.05,
                           channel=ch,range=mode,target_V=target)
                        op('matrix_current_read',lambda:typed_read(ch,'current'),lambda v:abs(v)<1.,
                           channel=ch,range=mode,current_A=limit,unit='mA')
                    op('matrix_ui_batch_snapshot',lambda:d.read_stable_snapshot(allch),
                       lambda v:len(v)==24 and all(abs(x-target)<=.05 and state for x,t,state in v),range=mode,target_V=target)
                    print(ip,'read matrix',mode,target,'V',limit,'A passed',flush=True)
        else:
            allch=list(range(1,25))
            checked(control_simulator(d,'range',allch,'HIGH',batch=True))
            for ch in allch:
                op('off_current',lambda:control_simulator(d,'current',[ch],.0005),channel=ch)
                for mode in ('LOW','AUTO','HIGH'):
                    op('off_range',lambda:control_simulator(d,'range',[ch],mode),channel=ch,range=mode)
                    op('off_range_preserved_state',lambda:d.read_output_state(ch),lambda x:x is False,channel=ch)
                op('off_voltage',lambda:control_simulator(d,'voltage',[ch],2.5),channel=ch)
                op('off_voltage_read',lambda:read_simulator(d,'voltage',ch),lambda x:x>=0,channel=ch)
                op('off_current_read',lambda:read_simulator(d,'current',ch),lambda x:abs(x)<1,channel=ch)
                op('reads_do_not_enable_output',lambda:d.read_output_state(ch),lambda x:x is False,channel=ch)
            op('off_batch_read',lambda:d.read_stable_snapshot(allch),lambda x:len(x)==24 and all(v[2] is False for v in x))
            d.disconnect()
            op('reconnect_on_configuration',lambda:control_simulator(d,'voltage',[1],1.))
            op('reconnect_preserves_off',lambda:d.read_output_state(1),lambda x:x is False)
            def cancel_before_on():
                try:control_simulator(d,'output',[1],True,cancelled=lambda:True)
                except RuntimeError:return d.read_output_state(1) is False
                return False
            op('cancel_prevents_enable',cancel_before_on)
            stop=threading.Event();original=d.set_range
            def interrupted(*args,**kwargs):
                result=original(*args,**kwargs);stop.set();return result
            d.set_range=interrupted
            def cancel_during_range():
                try:control_simulator(d,'range',[1],'AUTO',cancelled=stop.is_set)
                except RuntimeError:return d.read_output_state(1) is False
                return False
            try:op('cancel_during_range_preserves_off',cancel_during_range)
            finally:d.set_range=original
            checked(control_simulator(d,'range',[1,2],'HIGH',batch=True))
            checked(control_simulator(d,'current',[1,2],.01,batch=True))
            checked(control_simulator(d,'voltage_on',[1,2],1.,batch=True))
            def simultaneous(ch):
                values=(1.,2.5,4.2,2.5,1.) if ch==1 else (4.2,2.5,1.,2.5,4.2)
                samples=[]
                for value in values:
                    start=time.perf_counter()
                    checked(control_simulator(d,'voltage',[ch],value))
                    measured=read_simulator(d,'voltage',ch)
                    if abs(measured-value)>.05:raise RuntimeError(f'CH{ch}: {measured} != {value}')
                    samples.append(dict(channel=ch,target=value,voltage=measured,ms=(time.perf_counter()-start)*1000))
                return samples
            def parallel():
                with ThreadPoolExecutor(max_workers=2) as pool:
                    return list(pool.map(simultaneous,[1,2]))
            op('shared_instrument_concurrency',parallel,lambda x:len(x)==2 and all(len(v)==5 for v in x))
            print(ip,'edge cases passed',len(rows),flush=True)
    except Exception as exc:
        cleanup['test_error']=str(exc);print(ip,'FAILED',exc,flush=True)
    finally:
        try:
            cleanup['off_before_restore']=d.output_controls(False)
            if not cleanup['off_before_restore']:raise RuntimeError(d.last_error)
            cleanup['restore_errors']=[]
            for ch,s in baseline.items():
                for name,fn in [('range',lambda:d.set_range(ch,s['range'])),
                    ('current',lambda:d.set_current_limit(ch,s['current'])),
                    ('voltage',lambda:d.set_voltage(ch,s['voltage']))]:
                    if not fn():cleanup['restore_errors'].append([ch,name,d.last_error])
            cleanup['off']=d.output_controls(False)
            cleanup['states']=[d.read_output_state(ch) for ch in range(1,25)]
            cleanup['all_off']=all(x is False for x in cleanup['states'])
        except Exception as exc:cleanup['cleanup_error']=str(exc)
        d.disconnect();d.trace.close()
    result=dict(ip=ip,checks=len(rows),failures=sum(not r['ok'] for r in rows),cleanup=cleanup,rows=rows)
    (folder/(ip+'_edges.json')).write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--output',required=True);p.add_argument('--read-matrix-only',action='store_true');p.add_argument('--legacy-read-only',action='store_true');a=p.parse_args()
    if not a.execute:p.error('--execute required')
    folder=Path(a.output);folder.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda ip:run(ip,folder,a.read_matrix_only,a.legacy_read_only),['192.168.1.110','192.168.1.111','192.168.1.112']))
    summaries=[{k:v for k,v in r.items() if k!='rows'} for r in results]
    (folder/'summary.json').write_text(json.dumps(summaries,indent=2),encoding='utf-8')
    print(json.dumps(summaries,indent=2),flush=True)
    sys.exit(0 if all(r['failures']==0 and r['cleanup'].get('all_off') and not r['cleanup'].get('test_error') and
        not r['cleanup'].get('cleanup_error') and not r['cleanup'].get('restore_errors') for r in results) else 2)
