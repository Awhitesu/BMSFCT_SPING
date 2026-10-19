"""Run DBC logic from the EXE's PYZ, with user/site Python packages excluded."""
import argparse
import hashlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import sys
import time
from PyInstaller.archive.readers import CArchiveReader


def verify(exe,example,output):
    exe=Path(exe).resolve();internal=exe.parent/'_internal'
    pyz=CArchiveReader(str(exe)).open_embedded_archive('PYZ.pyz')
    required=['core.dbc_signals','ui.widgets.dbc_forms','cantools','cantools.database.can.formats.dbc','textparser']
    for name in required:
        if name not in pyz.toc:raise AssertionError('Missing frozen module: '+name)

    class BundleLoader(importlib.abc.MetaPathFinder,importlib.abc.Loader):
        def find_spec(self,fullname,path=None,target=None):
            if fullname not in pyz.toc:return None
            return importlib.util.spec_from_loader(fullname,self,is_package=pyz.toc[fullname][0] in (1,3))
        def create_module(self,spec):return None
        def exec_module(self,module):
            name=module.__name__;kind=pyz.toc[name][0]
            module.__file__=str(internal/Path(*name.split('.'))/('__init__.pyc' if kind in (1,3) else '__module__.pyc'))
            if kind in (1,3):module.__path__=[str(internal/Path(*name.split('.')))]
            if kind!=3:exec(pyz.extract(name),module.__dict__)

    # Keep only the host standard library and the bundle's extension-module tree.
    stdlib=Path(os.__file__).resolve().parent
    sys.path=[str(internal),str(stdlib),str(Path(sys.base_prefix)/'DLLs'),str(Path(sys.base_prefix)/'python311.zip')]
    roots=('core','cantools','can','bitstruct','textparser','argparse_addons','crccheck','wrapt','packaging','typing_extensions','yaml')
    for name in tuple(sys.modules):
        if name.split('.')[0] in roots:del sys.modules[name]
    sys.meta_path.insert(0,BundleLoader())
    sys.frozen=True;sys._MEIPASS=str(internal)
    from core.dbc_signals import database,DBCMonitor
    import cantools
    recipe=json.loads(Path(example).read_text(encoding='utf-8'));asset=recipe['dbc']
    db=database(asset);message=db.get_message_by_name('BMS_Status')
    data=message.encode(dict(PackVoltage=360,PackCurrent=-.2,SOC=68.8,BmsState=1,FaultCount=0,AliveCounter=7))
    decoded=message.decode(data,decode_choices=False)
    if decoded['PackVoltage']!=360 or decoded['PackCurrent']!=-.2:raise AssertionError('Frozen physical decoding mismatch')
    if str(message.decode(data)['BmsState'])!='Ready':raise AssertionError('Frozen enum decoding mismatch')
    class Driver:
        bus=object();channel='PCAN_USBBUS1';is_connected=True
        def __init__(self):self.listeners=set()
        def add_message_listener(self,callback):self.listeners.add(callback)
        def remove_message_listener(self,callback):self.listeners.discard(callback)
    driver=Driver();monitor=DBCMonitor();monitor.configure(asset,driver)
    try:
        for callback in driver.listeners:callback(dict(direction='RX',can_id=848,data=data,is_extended_id=False,interface=driver.channel))
        deadline=time.perf_counter()+2
        while not monitor.snapshot() and time.perf_counter()<deadline:time.sleep(.005)
        if len(monitor.snapshot())!=6:raise AssertionError('Frozen background decoder did not update all signals')
    finally:monitor.stop()
    if driver.listeners:raise AssertionError('Frozen decoder did not unsubscribe')
    result=dict(exe=str(exe),exe_sha256=hashlib.sha256(exe.read_bytes()).hexdigest(),cantools_version=cantools.__version__,
        verified=True,background_signals=6,modules={name:sys.modules[name].__file__ for name in ('cantools','core.dbc_signals','textparser')},
        scope='Code from embedded PYZ; host site-packages excluded. No GUI login, CAN hardware or device configuration accessed.')
    Path(output).write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps(result,ensure_ascii=False,indent=2))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--exe',required=True);parser.add_argument('--example',required=True);parser.add_argument('--output',required=True)
    args=parser.parse_args();verify(args.exe,args.example,args.output)
