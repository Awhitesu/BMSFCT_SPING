"""Authorized unloaded app-path acceptance. No production DB/config/recipe writes."""
import argparse
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from tools.test_simulator_hardware import Instrument
from core.simulator_control import control_simulator, read_simulator
from core.device_steps import encode, execute
from core.simulator_commands import execute_simulator_command
from devices.manager import DeviceManager


def exercise(ip,folder):
    d=Instrument(ip,folder)
    baseline={}
    rows=[]
    cleanup={}
    started=time.perf_counter()
    manager=DeviceManager.__new__(DeviceManager)
    manager.is_virtual=False
    manager._get_sim_and_ch=lambda ch:(d,ch) if type(ch) is int and 1<=ch<=24 else (None,None)
    manager.simulator_channels=lambda:[(ch,d,ch) for ch in range(1,25)]
    def checked(ok):
        if ok is not True:raise RuntimeError(d.last_error or 'Unconfirmed operation')
    def log(text):d.event('APP_LOG',text=text)
    def op(name,action,check=lambda v:v is True,**parameters):
        t=time.perf_counter()
        value=None
        error=''
        try:
            value=action()
            if not check(value):raise RuntimeError(f'Unexpected result: {value}')
        except Exception as exc:error=str(exc)
        row=dict(name=name,ok=not error,ms=round((time.perf_counter()-t)*1000,3),
                 result=value,error=error,**parameters)
        rows.append(row)
        d.event('SCENARIO',**row)
        if error:raise RuntimeError(name+': '+error)
        return value
    def follow(ch,target):
        values=[]
        start=time.perf_counter()
        while time.perf_counter()-start<.4:
            v=d.measure_voltage(ch)
            state=d.read_output_state(ch)
            values.append(v)
            if abs(v-target)>.05 or state is not True:
                raise RuntimeError(f'CH{ch} after return target={target}, voltage={v}, ON={state}')
            time.sleep(.075)
        return dict(min_V=min(values),max_V=max(values),samples=len(values))
    def live(action,channels,value,target):
        checked(control_simulator(d,action,channels,value,logger=log,batch=len(channels)>1))
        for ch in channels:
            if d.read_output_state(ch):follow(ch,target)
        return True
    def typed(action,values):
        return execute(manager,encode('simulator',action,values),{},log)
    try:
        op('connect',d.connect)
        for ch in range(1,25):
            settings=d.read_source_settings(ch)
            voltage=d.read_set_voltage(ch)
            state=d.read_output_state(ch)
            if settings is None or voltage is None or state is None:raise RuntimeError('Cannot save baseline')
            baseline[ch]=dict(settings,voltage=voltage,output=state)
        d.event('BASELINE',channels=baseline)
        checked(d.output_controls(False))
        for ch in range(1,25):
            checked(d.set_range(ch,'HIGH'))
            checked(d.set_current_limit(ch,.01))
        checked(d.set_voltages(1.))
        op('application_all_on',lambda:control_simulator(d,'output',list(range(1,25)),True,logger=log,batch=True))
        for ch in range(1,25):
            for voltage in (2.5,4.2,1.):
                op('single_live_voltage',lambda:live('voltage',[ch],voltage,voltage),channel=ch,target_V=voltage)
                op('independent_voltage_read',lambda:read_simulator(d,'voltage',ch,log),
                   lambda v:abs(v-voltage)<=.05,channel=ch,target_V=voltage)
            for current in (.01,.05,.1):
                op('single_live_current',lambda:live('current',[ch],current,1.),channel=ch,current_A=current)
                op('current_setting_readback',lambda:d.read_source_settings(ch),
                   lambda v:abs(v['current']-current)<.000011,channel=ch)
                op('independent_current_read',lambda:read_simulator(d,'current',ch,log),
                   lambda v:abs(v)<1.,channel=ch,unit='mA')
            checked(control_simulator(d,'current',[ch],.0005,logger=log))
            for mode in ('AUTO','LOW','HIGH'):
                op('single_live_range',lambda:live('range',[ch],mode,1.),channel=ch,range=mode)
                op('range_and_limit_readback',lambda:d.read_source_settings(ch),
                   lambda v:v['range']==mode and abs(v['current']-.0005)<.000011,channel=ch)
            op('typed_voltage_step',lambda:typed('voltage',{'channel':ch,'variable':'voltage'}),
               lambda v:v[0] is True and abs(float(v[1])-1.)<=.05,channel=ch)
            op('typed_current_step',lambda:typed('current',{'channel':ch,'variable':'current'}),
               lambda v:v[0] is True and abs(float(v[1]))<1.,channel=ch)
            if ch%4==0:print(ip,'single channels',ch,'/24','checks',len(rows),flush=True)

        # Mixed ON/OFF channel batch: configuring a stopped channel must not enable it.
        checked(control_simulator(d,'output',[2],False,logger=log))
        for action,value in [('voltage',2.5),('current',.0005),('range','LOW')]:
            op('mixed_state_batch_'+action,lambda:control_simulator(d,action,[1,2],value,logger=log,batch=True))
            op('mixed_state_preserved',lambda:[d.read_output_state(1),d.read_output_state(2)],
               lambda v:v==[True,False])
        checked(control_simulator(d,'range',[1,2],'HIGH',logger=log,batch=True))
        checked(control_simulator(d,'output',[2],True,logger=log))

        allch=list(range(1,25))
        for mode in ('HIGH','AUTO','LOW'):
            checked(control_simulator(d,'current',allch,.0005,logger=log,batch=True))
            op('batch_live_range',lambda:control_simulator(d,'range',allch,mode,logger=log,batch=True),range=mode)
            for current in ((.0001,.0005,.001) if mode=='LOW' else (.01,.05,.1)):
                op('batch_live_current',lambda:control_simulator(d,'current',allch,current,logger=log,batch=True),range=mode,current_A=current)
                settings=[d.read_source_settings(ch) for ch in allch]
                op('batch_current_readback',lambda:settings,
                   lambda v:all(x['range']==mode and abs(x['current']-current)<.000011 for x in v))
            for voltage in (1.,2.5,4.2):
                op('batch_live_voltage',lambda:control_simulator(d,'voltage',allch,voltage,logger=log,batch=True),range=mode,target_V=voltage)
                op('ui_batch_stable_read',lambda:d.read_stable_snapshot(allch),
                   lambda v:len(v)==24 and all(abs(a-voltage)<=.05 and state for a,t,state in v),range=mode,target_V=voltage)
            print(ip,'batch range',mode,'passed',flush=True)

        # Rejected LOW over-limit requests must preserve live output and settings.
        before=d.read_source_settings(1)
        def invalid_low():
            try:control_simulator(d,'current',[1],.01,logger=log)
            except ValueError:return d.read_source_settings(1)==before and d.read_output_state(1) is True
            return False
        op('reject_low_overlimit_without_write',invalid_low)
        op('typed_range_step',lambda:typed('range',{'channel':1,'range':'HIGH'}),lambda v:v[0] is True)
        op('typed_current_limit_step',lambda:typed('current_limit',{'channel':1,'current':.01}),lambda v:v[0] is True)
        op('typed_off_step',lambda:typed('off',{'channel':1}),lambda v:v[0] is True and d.read_output_state(1) is False)
        op('typed_on_step',lambda:typed('on',{'channel':1}),lambda v:v[0] is True and d.read_output_state(1) is True)
        op('legacy_low_recipe',lambda:execute_simulator_command(manager,'RANGE:LOW / 0.5mA / 2.5V / ON / CH:1',1,log),lambda v:v[0] is True)
        op('legacy_high_recipe',lambda:execute_simulator_command(manager,'RANGE:HIGH / 10mA / 1V / ON / CH:1',1,log),lambda v:v[0] is True)
        op('legacy_all_off',lambda:execute_simulator_command(manager,'OFF / CH:ALL',1,log),lambda v:v[0] is True)
        op('typed_all_on_step',lambda:typed('all_on',{}),lambda v:v[0] is True)
        op('typed_all_off_step',lambda:typed('all_off',{}),lambda v:v[0] is True)
        # OFF configuration and standalone reads never energize a channel.
        op('off_voltage_configuration',lambda:control_simulator(d,'voltage',[1],2.5,logger=log))
        op('off_state_preserved',lambda:d.read_output_state(1),lambda v:v is False)
        op('off_voltage_read_step',lambda:typed('voltage',{'channel':1}),lambda v:v[0] is True)
        op('off_state_after_read',lambda:d.read_output_state(1),lambda v:v is False)
    except Exception as exc:
        cleanup['test_error']=str(exc)
        print(ip,'FAILED',exc,flush=True)
    finally:
        try:
            cleanup['off_before_restore']=d.output_controls(False)
            if not cleanup['off_before_restore']:raise RuntimeError(d.last_error)
            cleanup['restore_errors']=[]
            for ch,setting in baseline.items():
                for name,fn in [('range',lambda:d.set_range(ch,setting['range'])),
                    ('current',lambda:d.set_current_limit(ch,setting['current'])),
                    ('voltage',lambda:d.set_voltage(ch,setting['voltage']))]:
                    if not fn():cleanup['restore_errors'].append([ch,name,d.last_error])
            cleanup['off']=d.output_controls(False)
            cleanup['states']=[d.read_output_state(ch) for ch in range(1,25)]
            cleanup['all_off']=all(v is False for v in cleanup['states'])
        except Exception as exc:cleanup['cleanup_error']=str(exc)
        d.disconnect()
        d.trace.close()
    result=dict(ip=ip,checks=len(rows),failures=sum(not r['ok'] for r in rows),
                duration_s=time.perf_counter()-started,cleanup=cleanup,rows=rows)
    (folder/(ip+'_application.json')).write_text(json.dumps(result,indent=2,default=str),encoding='utf-8')
    return result


if __name__=='__main__':
    p=argparse.ArgumentParser()
    p.add_argument('--execute',action='store_true')
    p.add_argument('--output',required=True)
    a=p.parse_args()
    if not a.execute:p.error('--execute required')
    folder=Path(a.output)
    folder.mkdir(parents=True,exist_ok=False)
    with ThreadPoolExecutor(max_workers=3) as pool:
        results=list(pool.map(lambda ip:exercise(ip,folder),
            ['192.168.1.110','192.168.1.111','192.168.1.112']))
    summaries=[{k:v for k,v in r.items() if k!='rows'} for r in results]
    (folder/'summary.json').write_text(json.dumps(summaries,indent=2),encoding='utf-8')
    print(json.dumps(summaries,indent=2),flush=True)
    sys.exit(0 if all(r['failures']==0 and r['cleanup'].get('all_off') and
        not r['cleanup'].get('restore_errors') and not r['cleanup'].get('test_error') and
        not r['cleanup'].get('cleanup_error') for r in results) else 2)
