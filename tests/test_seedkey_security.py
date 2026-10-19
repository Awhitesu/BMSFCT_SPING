import threading
import subprocess
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from core.seedkey import calculate_key, unlock, encode, decode, architecture
from devices.uds_client import UDSResponse
from ui.dialogs.step_dialog import StepDialog
import base64

app=QApplication.instance() or QApplication([])


@pytest.fixture(scope='module')
def dll(tmp_path_factory):
    root=tmp_path_factory.mktemp('seedkey-test')
    source=root/'dummy.c';target=root/'dummy.dll'
    source.write_text('''
    #include <stdint.h>
    #include <windows.h>
    __declspec(dllexport) int GenerateKeyEx(const unsigned char* seed, unsigned short len, unsigned int level, const char* variant, unsigned char* key, unsigned int cap, unsigned int* actual) {
      if(level==99) { Sleep(5000); }
      if(level==98) { *(volatile int*)0 = 0; }
      if(level==97) return 42;
      if(level==96) { *actual=cap+1; return 0; }
      if(cap<len) return 1;
      for(int i=0;i<len;i++) key[i]=seed[i]^level;
      *actual=len;return 0;
    }
    __declspec(dllexport) int ZLGKey(const unsigned char* seed, unsigned short len, unsigned int level, const char* variant, unsigned char* key, unsigned short* actual) {
      if(*actual<len) return 1;
      for(int i=0;i<len;i++) key[i]=seed[i]+level;
      *actual=len;return 0;
    }
    ''',encoding='utf-8')
    compiler=Path('C:/msys64/ucrt64/bin/gcc.exe')
    if not compiler.is_file():pytest.skip('native test compiler unavailable')
    subprocess.run([str(compiler),'-shared','-O2',str(source),'-o',str(target)],check=True)
    return target


def config(path,**changes):
    c=dict(path=str(path),api='GenerateKeyEx',calling='cdecl',level=1,dll_level=1,variant='',seed_data='',capacity=256,timeout=2)
    c.update(changes);return c


@pytest.mark.parametrize('api,expected',[('GenerateKeyEx',b'\x00\x03\x02\x05'),('auto',b'\x00\x03\x02\x05'),('ZLGKey',b'\x02\x03\x04\x05')])
def test_real_native_dll_isolated(api,expected,dll):
    c=config(dll,api=api)
    assert architecture(dll)=='x64'
    assert calculate_key(b'\x01\x02\x03\x04',c)==expected
    assert decode(encode(c))==c


@pytest.mark.parametrize('level,pattern',[(99,'超时'),(98,'异常退出|超时'),(97,'42'),(96,'length')])
def test_native_crash_hang_and_invalid_output_do_not_crash_host(dll,level,pattern):
    with pytest.raises(RuntimeError,match=pattern):
        calculate_key(b'\x01\x02',config(dll,dll_level=level,timeout=.3))


def fake_client(seed=b'\x01\x02',negative=False):
    client=SimpleNamespace(_lock=threading.RLock())
    client.security_access_request_seed=Mock(return_value=UDSResponse(0x27,b'\x67\x01'+seed,True,0x67,b'\x01'+seed))
    client.security_access_send_key=Mock(return_value=UDSResponse(0x27,b'\x67\x02',not negative,0x67,b'\x02',0x35 if negative else None))
    return client


def test_security_handshake_and_already_unlocked(dll):
    client=fake_client();logger=Mock()
    assert unlock(client,config(dll),logger).positive
    client.security_access_send_key.assert_called_once_with(2,b'\x00\x03')
    client=fake_client(b'\x00\x00')
    assert unlock(client,config(dll),algorithm=Mock(side_effect=AssertionError())).positive
    client.security_access_send_key.assert_not_called()


def test_negative_response_and_cancel_block_key(dll):
    client=fake_client(negative=True)
    with pytest.raises(RuntimeError,match='不会自动重试'):
        unlock(client,config(dll))
    assert client.security_access_send_key.call_count==1
    client=fake_client()
    stopped=threading.Event()
    def algorithm(seed,c):stopped.set();return b'AB'
    with pytest.raises(RuntimeError,match='取消'):
        unlock(client,config(dll),cancelled=stopped.is_set,algorithm=algorithm)
    client.security_access_send_key.assert_not_called()


def test_uds_editor_security_row_and_configuration_roundtrip(dll):
    page=StepDialog()
    try:
        page.category_combo.setCurrentText('报文交互')
        page.device_combo.setCurrentText('UDS交互')
        # Use an actual UDS device from the selector, retaining label compatibility.
        for i in range(page.device_combo.count()):
            if 'UDS' in page.device_combo.itemText(i):page.device_combo.setCurrentIndex(i);break
        page.uds_security.load(config(dll))
        page._add_security_unlock_row()
        assert '@SECURITY' in page._uds_script()
        saved=page.get_data()
        assert 'SEC64:' in saved['params']
        restored=StepDialog(step_data=saved)
        try:
            assert restored.uds_security.config()==config(dll)
            assert '@SECURITY' in restored._uds_script()
        finally:restored.close()
    finally:page.close()


@pytest.mark.parametrize('negative',[False,True])
def test_engine_unlock_is_between_session_and_write_and_blocks_on_failure(dll,negative):
    from core.engine import ChannelWorker,SubStep,SubStepType
    client=fake_client(negative=negative)
    client.request=Mock(side_effect=[UDSResponse(0x10,b'\x50\x03',True,0x50,b'\x03'),
                                    UDSResponse(0x2E,b'\x6E\xF1\x90',True,0x6E,b'\xF1\x90')])
    driver=Mock();driver.is_connected=True;driver.default_can_type=0;driver.create_uds_client.return_value=client
    worker=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver))
    script=base64.urlsafe_b64encode(b'10 03 => 50 03\n@SECURITY\n2E F1 90 01 => 6E F1 90').decode()
    sub=SubStep(SubStepType.CAN_INTERACT,dict(device='UDS',params=f'LIST64:{script} / SEC64:{encode(config(dll))} / INTERVAL:0'))
    ok,_=worker._execute_sub_step_logic(sub)
    assert ok is not negative
    assert client.request.call_count==(1 if negative else 2)
    assert client.security_access_request_seed.call_count==1
    assert client.security_access_send_key.call_count==1


def test_real_x86_dll_bridge(tmp_path):
    compiler=Path('C:/msys64/ucrt64/bin/gcc.exe')
    if not compiler.is_file():pytest.skip('native compiler unavailable')
    source=tmp_path/'dummy32.c';target=tmp_path/'dummy32.dll'
    source.write_text('''
    __declspec(dllexport) int GenerateKeyEx(const unsigned char* seed, unsigned short len, unsigned int level, const char* variant, unsigned char* key, unsigned int cap, unsigned int* actual) {
      if(cap<len) return 1;
      for(int i=0;i<len;i++)key[i]=seed[i]^level;
      *actual=len;return 0;
    }
    __declspec(dllexport) int __stdcall ZLGKey(const unsigned char* seed, unsigned short len, unsigned int level, const char* variant, unsigned char* key, unsigned short* actual) {
      if(*actual<len)return 1;
      for(int i=0;i<len;i++)key[i]=seed[i]+level;
      *actual=len;return 0;
    }
    ''',encoding='utf-8')
    subprocess.run([str(compiler),'-m32','-shared','-nostdlib','-O2','-Wl,--entry,0',str(source),'-o',str(target)],check=True)
    assert architecture(target)=='x86'
    assert calculate_key(b'\x01\x02',config(target))==b'\x00\x03'
    assert calculate_key(b'\x01\x02',config(target,api='ZLGKey',calling='stdcall'))==b'\x02\x03'


def test_frozen_installed_helper_is_used_with_real_dll(dll,tmp_path,monkeypatch):
    import sys,shutil
    from core.seedkey import helper_path
    source=helper_path('x64')
    installed=tmp_path/'_internal/resources/seedkey/SeedKeyHost-x64.exe'
    installed.parent.mkdir(parents=True);shutil.copy2(source,installed)
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(tmp_path/'BMSFCT.exe'))
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path/'other-bundle'),raising=False)
    assert helper_path('x64')==installed
    assert calculate_key(b'\x01\x02',config(dll))==b'\x00\x03'
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path/'_internal'))
    assert helper_path('x64')==installed


def test_missing_frozen_helper_reports_installation_and_checked_paths(tmp_path,monkeypatch):
    import sys
    from core.seedkey import helper_path
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'executable',str(tmp_path/'BMSFCT.exe'))
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path/'_internal'),raising=False)
    with pytest.raises(RuntimeError) as exc:helper_path('x86')
    assert str(tmp_path/'_internal/resources/seedkey/SeedKeyHost-x86.exe') in str(exc.value)
    assert 'build_seedkey_hosts' not in str(exc.value)


def test_missing_helper_blocks_seed_request_before_ecu_is_touched(dll,monkeypatch):
    import core.seedkey as module
    client=fake_client()
    def missing(_):raise RuntimeError('missing helper')
    monkeypatch.setattr(module,'helper_path',missing)
    with pytest.raises(RuntimeError,match='missing helper'):unlock(client,config(dll))
    client.security_access_request_seed.assert_not_called()
    client.security_access_send_key.assert_not_called()


def test_debug_security_page_and_worker(dll):
    from ui.tabs.pcan_fd_tab import PCANFDTab,UDSRequestWorker
    from devices.pcan_fd_driver import PCANFDDriver
    manager=SimpleNamespace(pcan=PCANFDDriver())
    page=PCANFDTab(manager)
    try:
        page.timer.stop()
        assert page.uds_detail_tabs.count()==2
        page.uds_security.load(config(dll))
        assert page.uds_security.config()==config(dll)
        client=fake_client()
        worker=UDSRequestWorker(client,0x27,b'',security=config(dll))
        results=[];worker.result.connect(results.append)
        worker.run()
        assert len(results)==1 and results[0].positive
        assert client.security_access_send_key.call_count==1
    finally:
        page.shutdown();page.close()
