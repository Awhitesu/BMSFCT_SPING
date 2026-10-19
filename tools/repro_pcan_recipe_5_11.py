"""One bounded, explicitly requested hardware reproduction; no automatic retries."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import json
import threading
import time
from datetime import datetime
from types import SimpleNamespace
from PySide6.QtCore import QCoreApplication
from core.engine import ChannelWorker, SubStep, SubStepType
from devices.pcan_fd_driver import PCANFDDriver


def main():
    app = QCoreApplication.instance() or QCoreApplication([])
    recipe = json.loads(Path('recipes/VF-Limo7(PVFLIMO7-VN)_APP.json').read_text(encoding='utf-8'))
    out = Path('logs/hardware') / ('pcan_5_11_' + datetime.now().strftime('%Y%m%d_%H%M%S') + '.jsonl')
    out.parent.mkdir(parents=True, exist_ok=True)
    lock = threading.Lock()
    with out.open('w', encoding='utf-8') as stream:
        def record(kind, **data):
            with lock:
                stream.write(json.dumps(dict(time=datetime.now().isoformat(), kind=kind, **data), ensure_ascii=True, default=str)+'\n')
                stream.flush()
        cfg=recipe['can_config']
        driver=PCANFDDriver(cfg['channel'],cfg['nominal_bitrate'],cfg['data_bitrate'],fd_enabled=True,logical_channel=1)
        driver.default_can_type=2
        def frame(info):
            record('frame', **{k:(v.hex(' ').upper() if isinstance(v,bytes) else v) for k,v in info.items()})
        driver.add_message_listener(frame)
        worker=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver))
        worker.log_message.connect(lambda ch,text:record('engine',text=text))
        record('begin', can_config=cfg, items=[recipe['items'][i-1] for i in (5,11)])
        try:
            if not driver.connect():
                record('connection_failed',error=driver.last_error)
                print('CONNECTION_FAILED',driver.last_error)
                return
            record('connected')
            time.sleep(.5)
            for index in (5,11):
                for sub in recipe['items'][index-1]['sub_steps']:
                    if sub['type']=='\u7b49\u5f85':
                        time.sleep(.5)
                        continue
                    driver.check_bus_health()
                    record('step_begin',item=index,name=sub['name'])
                    ok,value=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,sub))
                    record('step_end',item=index,success=ok,value=value)
                    print('ITEM',index,'OK',ok,'VALUE',str(value).encode('ascii','backslashreplace').decode(),flush=True)
                    if not ok:
                        return
            time.sleep(.5)
            driver.check_bus_health()
            record('completed')
        except Exception as exc:
            record('exception',error=str(exc))
            print('ERROR',str(exc).encode('ascii','backslashreplace').decode())
        finally:
            record('cleanup',errors=worker.periodic_can.close(),fault=driver._bus_fault)
            driver.disconnect()
            print('LOG',out.resolve(),flush=True)


if __name__=='__main__':
    main()
