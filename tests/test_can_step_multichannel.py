from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading

import pytest
from PySide6.QtWidgets import QApplication, QWidget, QMessageBox
from core.can_step_channels import migrate_recipe_channels, resolve_channels, validate_step_channels
from core.engine import ChannelWorker, SubStep, SubStepType
from devices.pcan_fd_driver import PCANFDDriver
from ui.dialogs.step_dialog import StepDialog
from ui.dialogs.zlg_recipe_dialog import ZlgRecipeDialog
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


def binding(enabled=(1,3,4), channel=3):
    return dict(channel=f'ZLG_CAN{channel}',mode='经典 CAN',zlg=dict(
        ip='192.0.2.10',port=8000,send_channel=channel,
        channels=[dict(enabled=i in enabled,nominal=500000,data=2000000,termination=False) for i in range(1,5)]))


def send(token='ALL'):
    return dict(name='send',device='CAN 交互',action='发送指令',type='CAN发送',
                params=f'ID:0x123 / DATA:01 02 / DLC:2 / TYPE:0 / PCH:{token}',is_judgment=False)


def recipe(step=None):
    return dict(name='routing',can_config=binding(),items=[dict(name='parent',step_id='parent',min='1',max='2',
        sub_steps=[dict(step_id='child',**(step or send()))])])


class Parent(QWidget):
    def recipe_can_binding(self):return binding()


@pytest.mark.parametrize('token',['1,3','ALL','4'])
def test_send_editor_multiselect_and_all_roundtrip(token):
    parent=Parent();page=StepDialog(parent,step_data=send(token))
    restored=None
    try:
        assert page.can_physical_channel.isHidden() and not page.can_send_channels.isHidden()
        assert page.get_data()['params'].endswith('PCH:'+token)
        assert not page.can_send_checks[2].isEnabled()
        saved=page.get_data();restored=StepDialog(parent,step_data=saved)
        assert restored.get_data()['params']==saved['params']
        if token=='ALL':
            assert not any(check.isEnabled() for check in page.can_send_checks.values())
            assert {ch for ch,check in page.can_send_checks.items() if check.isChecked()}=={1,3,4}
        page.action_combo.setCurrentText('接收指定帧ID')
        assert page.can_send_channels.isHidden() and not page.can_physical_channel.isHidden()
        assert ',' not in page.get_data()['params'].split('PCH:')[-1]
    finally:
        if restored:restored.close()
        page.close();parent.close()


def test_empty_channel_selection_cannot_be_accepted():
    parent=Parent();page=StepDialog(parent,step_data=send('1,3'))
    try:
        for check in page.can_send_checks.values():check.setChecked(False)
        with patch.object(QMessageBox,'warning') as warning:
            page.accept();warning.assert_called_once()
        assert page.result()==0
        with pytest.raises(ValueError,match='至少选择'):page.get_data()
    finally:page.close();parent.close()


def test_all_indicates_enabled_ports_and_restores_custom_selection_when_unchecked():
    parent=Parent();page=StepDialog(parent,step_data=send('1,4'))
    try:
        page.can_send_all.setChecked(True)
        assert {ch for ch,check in page.can_send_checks.items() if check.isChecked()}=={1,3,4}
        page.can_send_all.setChecked(False)
        assert page.get_data()['params'].endswith('PCH:1,4')
    finally:page.close();parent.close()


def test_old_default_port_stays_explicit_and_unrelated_data_is_unchanged():
    data=recipe(send('0'));original=deepcopy(data)
    assert migrate_recipe_channels(data)==1
    assert data['items'][0]['sub_steps'][0]['params'].endswith('PCH:3')
    original['items'][0]['sub_steps'][0]['params']=data['items'][0]['sub_steps'][0]['params']
    original['can_config']=data['can_config']
    assert data==original
    assert migrate_recipe_channels(data)==0
    validate_step_channels(data)


def test_editor_load_freezes_old_channel_and_disabled_old_channel_is_not_rerouted():
    db=Mock();db.load_sys_config.return_value={};db.list_recipes.return_value=[]
    page=ConfigTab(db);original=recipe(send('0'))
    try:
        page.load_recipe_to_tree(original)
        assert original['items'][0]['sub_steps'][0]['params'].endswith('PCH:0')
        assert page.step_tree.topLevelItem(0).child(0).text(2).endswith('PCH:3')
        saved=page._collect_recipe_data('routing')
        assert saved['items'][0]['sub_steps'][0]['params'].endswith('PCH:3')
        updated=deepcopy(binding(enabled=(1,4),channel=1)['zlg'])
        with patch.object(ZlgRecipeDialog,'exec',return_value=1),patch.object(ZlgRecipeDialog,'get_config',return_value=updated):
            page.configure_recipe_zlg()
        with pytest.raises(ValueError,match='CH3未启用'):page._collect_recipe_data('routing')
        assert page.step_tree.topLevelItem(0).child(0).text(2).endswith('PCH:3')
    finally:page.close()


def test_preparation_freezes_legacy_port_before_any_connection_change():
    driver=Mock();driver.lock=threading.RLock();driver.connect.return_value=True;driver.disconnect.return_value=True
    page=SimpleNamespace(_prepare_cancel=threading.Event(),engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=driver)))
    data=recipe(send('0'))
    assert OverviewTab._apply_recipe_hardware(page,data)[0]
    assert data['items'][0]['sub_steps'][0]['params'].endswith('PCH:3')
    driver.bus.configure_channels.assert_called_once_with(data['can_config']['zlg']['channels'],False)


@pytest.mark.parametrize('token',['1,3','ALL'])
@pytest.mark.parametrize('kind',['UDS 交互列表','CANDBC 交互','3.5HEOL协议'])
def test_response_and_dbc_steps_still_require_one_port(token,kind):
    data=recipe(dict(send(token),device=kind,type=kind,action='读取'))
    with pytest.raises(ValueError,match='单一'):validate_step_channels(data)


def driver_and_worker(enabled=(0,2,3),pcan=False):
    driver=PCANFDDriver('PCAN_USBBUS1' if pcan else 'ZLG_CAN3')
    driver.is_connected=True;driver.bus=Mock();driver.bus.enabled_channels=set(enabled)
    worker=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver,boards={}))
    worker.is_running=True
    return driver,worker


@pytest.mark.parametrize('token,expected',[('ALL',[0,2,3]),('1,3',[0,2]),('4',[3]),('0',[2])])
def test_actual_send_routes_to_each_selected_port_once_without_switching_adapter(token,expected):
    driver,worker=driver_and_worker()
    try:
        ok,_=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_SEND,send(token)))
        assert ok
        messages=[call.args[0] for call in driver.bus.send.call_args_list]
        assert [message.channel for message in messages]==expected
        assert all(message.arbitration_id==0x123 and bytes(message.data)==b'\x01\x02' for message in messages)
        assert driver.channel=='ZLG_CAN3'
    finally:worker.is_running=False


@pytest.mark.parametrize('token',['1,2','1,1','ALL','5'])
def test_invalid_selection_is_rejected_before_sending_anything(token):
    driver,worker=driver_and_worker(enabled=() if token=='ALL' else (0,2))
    try:
        ok,_=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_SEND,send(token)))
        assert not ok;driver.bus.send.assert_not_called()
    finally:worker.is_running=False


def test_failed_port_fails_step_and_identifies_port_after_sending_remaining_selected_ports():
    driver,worker=driver_and_worker()
    def transmit(message,**kwargs):
        if message.channel==2:raise ConnectionError('offline')
    driver.bus.send.side_effect=transmit
    try:
        ok,result=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_SEND,send()))
        assert not ok and 'CH3' in result and 'offline' in result
        assert [call.args[0].channel for call in driver.bus.send.call_args_list]==[0,2,3]
    finally:worker.is_running=False


def test_cancellation_does_not_send_remaining_channels():
    driver,worker=driver_and_worker()
    driver.bus.send.side_effect=lambda *args,**kwargs:worker.cancel_event.set()
    try:
        ok,result=worker._execute_sub_step_logic(SubStep(SubStepType.CAN_SEND,send()))
        assert not ok and '停止' in result and driver.bus.send.call_count==1
    finally:worker.is_running=False


def test_pcan_all_still_uses_its_only_physical_port_and_rejects_more():
    driver,worker=driver_and_worker(pcan=True)
    try:
        assert worker._execute_sub_step_logic(SubStep(SubStepType.CAN_SEND,send()))[0]
        assert driver.bus.send.call_count==1
        with pytest.raises(ValueError):resolve_channels('PCH:1,2',maximum=1,multiple=True)
    finally:worker.is_running=False
