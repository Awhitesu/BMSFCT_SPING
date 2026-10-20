import importlib.util
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import can
import pytest
from PySide6.QtWidgets import QApplication, QWidget

from devices.pcan_fd_driver import PCANFDDriver
from devices.can_channel_view import CANChannelView
from devices.zlg_net_bus import ZLGNetBus
from ui.dialogs.step_dialog import StepDialog
from core.engine import ChannelWorker

app=QApplication.instance() or QApplication([])


class RecipeParent(QWidget):
    def __init__(self, channel):
        super().__init__();self.channel=channel
    def recipe_can_binding(self):
        return dict(channel=self.channel)


@pytest.mark.parametrize('channel,count',[('PCAN_USBBUS1',2),('ZLG_CAN2',5)])
def test_editor_inherits_recipe_and_roundtrips_physical_channel(channel,count):
    parent=RecipeParent(channel)
    page=StepDialog(parent)
    try:
        assert page.can_physical_channel.count()==count
        page.category_combo.setCurrentText('报文交互')
        page.device_combo.setCurrentText('CAN 交互')
        page.action_combo.setCurrentText('发送指令')
        page.can_physical_channel.setCurrentIndex(count-1)
        saved=page.get_data()
        assert f'PCH:{count-1}' in saved['params']
        restored=StepDialog(parent,step_data=saved)
        try:assert restored.can_physical_channel.currentData()==count-1
        finally:restored.close()
        assert page.c_channel.isHidden()
        assert ('ZLG' if channel.startswith('ZLG') else 'PCAN_USBBUS1') in page.can_binding_label.text()
    finally:page.close();parent.close()


def driver():
    d=PCANFDDriver('ZLG_CAN1');d.is_connected=True;d.bus=Mock()
    d.bus.enabled_channels={0,1,2,3}
    return d


def test_send_routes_to_selected_channel_without_mutating_default():
    d=driver();view=CANChannelView(d,3)
    assert view.send_can_message(0,0x123,0,2,b'AB')
    assert d.bus.send.call_args.args[0].channel==2
    assert d.channel=='ZLG_CAN1'
    info=d.msg_queue.get_nowait()
    assert info['physical_channel']==3 and info['interface']=='ZLG_CAN3'


def test_same_id_on_other_channel_cannot_satisfy_response():
    d=driver();view=CANChannelView(d,3);now=time.time()
    for index in (0,2):
        d._publish(dict(channel=index,direction='RX',can_id=0x708,data=bytes([index]),timestamp_rel=now))
    message=view.wait_for_message(0x708,timeout=0)
    assert message['data']==b'\x02'
    view.clear_rx_history(0x708)
    assert len(d._rx_history)==1 and d._rx_history[0]['channel']==0


def test_uds_transport_binds_both_tx_and_rx():
    d=driver();view=CANChannelView(d,4)
    client=view.create_uds_client()
    client.transport.send(b'\x22\xf1\x90')
    assert d.bus.send.call_args.args[0].channel==3
    assert client.transport.driver is view
    assert client.transport._lock is d._request_lock


def test_pcan_and_disabled_zlg_channels_rejected():
    with pytest.raises(ValueError):CANChannelView(PCANFDDriver(),2)
    d=driver();d.bus.enabled_channels={0}
    with pytest.raises(ValueError):CANChannelView(d,3)


def test_engine_step_uses_shared_recipe_adapter_and_channel():
    d=driver();mgr=SimpleNamespace(pcan=d,boards={})
    worker=ChannelWorker(1,[],device_manager=mgr)
    board=worker._get_can_board(mgr,{'params':'PCH:2'})
    assert board.can.driver is d and board.can.physical_channel==2
    assert worker._get_can_board(mgr,{'params':'CH:99'}).can is d


def test_zlg_transmit_uses_correct_native_handle():
    root=Path(__file__).resolve().parents[1]
    spec=importlib.util.spec_from_file_location('test_route_types',root/'vendor/zlgcan_python_260529/zlgcan.py')
    types=importlib.util.module_from_spec(spec);spec.loader.exec_module(types)
    bus=ZLGNetBus.__new__(ZLGNetBus)
    bus.types=types;bus.lock=threading.RLock();bus.physical_channel=0
    bus.handle=100;bus.handles={0:100,1:101,2:102,3:103};bus.enabled_channels={0,1,2,3}
    bus.dll=Mock();bus.dll.ZCAN_Transmit.return_value=1
    bus.send(can.Message(arbitration_id=0x123,data=b'AB',channel=2))
    assert bus.dll.ZCAN_Transmit.call_args.args[0]==102
    assert bus.handle==100 and bus.physical_channel==0
