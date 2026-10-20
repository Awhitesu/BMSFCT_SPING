"""No-transmit, no dynamic configuration LAN/SDK receive test."""
import argparse
import json
from pathlib import Path
import sys
import time

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from devices.zlg_net_bus import ZLGNetBus

def run(folder,ip,sdk):
    folder.mkdir(exist_ok=False,parents=True)
    rows=[];bus=None;result={'ip':ip,'port':8000,'transmit_calls':0,'dynamic_configuration_calls':0,'cycles':rows}
    try:
        for cycle in range(10):
            row={'cycle':cycle+1};rows.append(row)
            start=time.perf_counter()
            bus=ZLGNetBus(sdk,ip,8000,channel=cycle%4)
            row['open_ms']=(time.perf_counter()-start)*1000
            row['channels']=sorted(bus.handles)
            assert row['channels']==[0,1,2,3]
            row['rx_calls']=0;row['received']=0;row['max_recv_ms']=0
            deadline=time.monotonic()+(15 if cycle==0 else 3)
            while time.monotonic()<deadline:
                start=time.perf_counter();msg=bus.recv(.05)
                row['max_recv_ms']=max(row['max_recv_ms'],(time.perf_counter()-start)*1000)
                row['rx_calls']+=1
                if msg is not None:
                    row['received']+=1
                    with (folder/'frames.jsonl').open('a',encoding='utf-8') as f:
                        f.write(json.dumps({'cycle':cycle+1,'channel':msg.channel,'id':msg.arbitration_id,'fd':msg.is_fd,'data':bytes(msg.data).hex()})+'\n')
            start=time.perf_counter();bus.shutdown()
            row['close_ms']=(time.perf_counter()-start)*1000
            row['closed']=bus.device is None and not bus.handles
            assert row['closed'];row['ok']=True;bus=None
            print(json.dumps(row),flush=True)
    except Exception as exc:
        result['error']=str(exc);print('ERROR',str(exc),flush=True)
    finally:
        if bus is not None:
            try:bus.shutdown()
            except Exception as exc:result['cleanup_error']=str(exc)
        result['passed']=len(rows)==10 and all(r.get('ok') for r in rows) and not result.get('cleanup_error')
        (folder/'summary.json').write_text(json.dumps(result,indent=2),encoding='utf-8')
    return result

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--ip',default='192.168.1.106');p.add_argument('--sdk',default='vendor/zlgcan(20260806)/zlgcan_x64');a=p.parse_args()
    sys.exit(0 if run(Path(a.output),a.ip,a.sdk)['passed'] else 2)
