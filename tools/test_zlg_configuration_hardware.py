"""Authorized volatile ZLG rate/termination test; restores user-supplied baseline."""
import argparse
import ctypes as C
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from devices.zlg_net_bus import ZLGNetBus

def run(folder,cycles=12):
    folder.mkdir(parents=True,exist_ok=False)
    baseline=[dict(enabled=True,nominal=500000,data=2000000,termination=True) for _ in range(4)]
    result=dict(baseline_source='User: all CH1-CH4 500K / 2M / termination ON',baseline=baseline,
                verification='SDK return codes and receive polling only; no physical rate/resistance readback',rows=[])
    bus=None
    trace=(folder/'sdk.jsonl').open('w',encoding='utf-8')
    try:
        bus=ZLGNetBus('zlgcan(20260806)/zlgcan_x64','192.168.1.106',8000)
        real=bus.dll
        class TracedDLL:
            def __getattr__(self,name):return getattr(real,name)
            def ZCAN_SetValue(self,device,path,value):
                extra={}
                if path==b'0/add_dynamic_data':
                    obj=C.cast(value,C.POINTER(bus.types.ZCAN_DYNAMIC_CONFIG_DATA)).contents
                    extra=dict(key=obj.key.decode(),value=obj.value.decode())
                elif path==b'0/apply_dynamic_data':extra=dict(persist=int(C.cast(value,C.POINTER(C.c_uint)).contents.value))
                start=time.perf_counter();ret=real.ZCAN_SetValue(device,path,value)
                trace.write(json.dumps(dict(path=path.decode(),result=int(ret),ms=(time.perf_counter()-start)*1000,**extra))+'\n');trace.flush()
                return ret
        bus.dll=TracedDLL()
        def case(name,settings,action):
            start=time.perf_counter();row=dict(name=name,settings=settings);result['rows'].append(row)
            try:
                action();row['apply_ms']=(time.perf_counter()-start)*1000
                row['received']=sum(bus.recv(.02) is not None for _ in range(3))
                row['ok']=True
            except Exception as exc:row['error']=str(exc);row['ok']=False;raise
            finally:
                print(json.dumps(row),flush=True)
                (folder/'progress.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
        # All-channel application, independently varying rates and termination.
        nominals=(50000,100000,125000,250000,500000,800000,1000000)
        data_rates=(100000,125000,250000,500000,800000,1000000,2000000,4000000,5000000)
        for cycle in range(cycles):
            settings=[dict(enabled=True,nominal=nominals[(cycle+ch)%len(nominals)],
                data=data_rates[(cycle//2+ch)%len(data_rates)],termination=bool((cycle+ch)%2)) for ch in range(4)]
            case('four_channels_'+str(cycle+1),settings,lambda:bus.configure_channels(settings,persist=False))
        # Exercise the single-channel application entry for each physical channel.
        for ch in range(4):
            bus.physical_channel=ch;bus.handle=bus.handles[ch]
            for term in (False,True):
                setting=dict(channel=ch,nominal=250000,data=1000000,termination=term)
                case('single_channel',setting,lambda:bus.configure_channel(250000,1000000,term,persist=False))
    except Exception as exc:result['error']=str(exc)
    finally:
        if bus is not None:
            try:
                start=time.perf_counter();bus.configure_channels(baseline,persist=False)
                result['restore']=dict(sdk_accepted=True,ms=(time.perf_counter()-start)*1000,
                    physical_readback_verified=False)
                result['final_recv']=bus.recv(.05) is not None
            except Exception as exc:result['restore']=dict(sdk_accepted=False,error=str(exc))
            try:bus.shutdown();result['closed']=bus.device is None
            except Exception as exc:result['close_error']=str(exc)
        trace.close()
        result['sdk_test_passed']=len(result['rows'])==cycles+8 and all(x.get('ok') for x in result['rows']) and result.get('restore',{}).get('sdk_accepted',False) and result.get('closed',False)
        (folder/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps({k:v for k,v in result.items() if k!='rows'},indent=2),flush=True)
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--execute',action='store_true');p.add_argument('--output',required=True);p.add_argument('--cycles',type=int,default=12);a=p.parse_args()
    if not a.execute:p.error('--execute required; changes live volatile settings')
    sys.exit(0 if run(Path(a.output),a.cycles)['sdk_test_passed'] else 2)
