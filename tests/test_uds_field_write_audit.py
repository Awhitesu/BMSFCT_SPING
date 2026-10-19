import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import base64
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch
import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from core.uds_field_write import encode, decode, execution_values, build_details, DEVICE
from core.uds_sequence import settings_from
from core.engine import ChannelWorker, SubStep, SubStepType
from devices.uds_client import UDSResponse

app = QApplication.instance() or QApplication([])


def config(**extra):
    return dict(dict(tx_id=0x7e0,rx_id=0x7e8,did=0x1e5e,literal='AB',start=2,length=4,timeout=1000),**extra)


def setup_driver():
    client = MagicMock()
    client.request.return_value = UDSResponse(0x2e,bytes.fromhex('6e 1e 5e'),True,0x6e,bytes.fromhex('1e 5e'))
    driver = MagicMock()
    driver.is_connected = True; driver.channel = 'PCAN_USBBUS1'
    driver.default_can_type = 0; driver.nominal_bitrate = 500000; driver.data_bitrate = 2000000
    driver.create_uds_client.return_value = client
    return driver,client


def test_advanced_transport_settings_compile_and_reach_standard_client():
    settings = dict(frame_format='extended',padding=0xaa,block_size=8,st_min=5,p2_star_ms=9000)
    c = config(uds_settings=settings)
    assert decode(encode(c)) == c
    compiled,text = execution_values(c,'123456789',datetime(2026,10,19))
    assert settings_from(compiled['UDSCFG64'])['padding'] == 0xaa
    script = base64.urlsafe_b64decode(compiled['LIST64']).decode()
    assert script.startswith('2E 1E 5E ') and 'SEC64' not in compiled
    driver,client = setup_driver()
    worker = ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver))
    worker.sn_barcode = '123456789'; worker._uds_field_write_now = datetime(2026,10,19)
    ok,value = worker._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device=DEVICE,params='WRITE64:'+encode(c))))
    assert ok and value == '6E'
    assert worker.variables['主板序列号'] == text
    call = driver.create_uds_client.call_args.kwargs
    assert call['padding'] == 0xaa and call['block_size'] == 8 and call['st_min'] == 5
    assert call['p2_star_timeout'] == 9
    assert client.transport.extended_id is True
    assert client.request.call_count == 1 and client.request.call_args.args[0] == 0x2e


@pytest.mark.parametrize('settings', [dict(st_min=0x80),dict(padding=256),dict(p2_star_ms=0),dict(address='functional')])
def test_invalid_advanced_settings_rejected_before_request(settings):
    with pytest.raises(ValueError):
        encode(config(uds_settings=settings))
    with pytest.raises(ValueError):
        encode(config(tx_id=0x18DA10F1,uds_settings=dict(frame_format='standard')))


def test_legacy_configuration_does_not_change_transport_defaults():
    compiled,_ = execution_values(config(),'123456789')
    assert 'UDSCFG64' not in compiled
    details = build_details(config(),'123456789',datetime(2026,10,19))
    assert details['literal'] == 'AB' and details['date'] == '261019' and details['barcode_slice'] == '2345'
    assert details['text'] == 'AB2610192345'


def test_explicit_default_settings_can_override_legacy_p2_star_policy():
    from ui.dialogs.step_dialog import StepDialog
    dialog = StepDialog()
    try:
        form = dialog.uds_field_write_form
        form.load(config(timeout=2000))
        assert 'uds_settings' not in form.config()
        form.use_transport.setChecked(True)
        assert form.config()['uds_settings']['p2_star_ms'] == 5000
        compiled,_ = execution_values(form.config(),'123456789')
        assert settings_from(compiled['UDSCFG64'])['p2_star_ms'] == 5000
        assert type(form.transport_settings) is type(dialog.uds_workbench.transport_settings)
    finally:
        dialog.close()


def test_capture_freezes_controls_and_uses_one_snapshot_even_if_widgets_change():
    from ui.dialogs.step_dialog import StepDialog
    driver,client = setup_driver()
    dialog = StepDialog(device_manager=SimpleNamespace(pcan=driver))
    form = dialog.uds_field_write_form
    pending = {}
    form.task.run = lambda action,done: pending.update(action=action,done=done)
    try:
        form.load(config());form.sample.setText('123456789')
        dialog.btn_ok.setEnabled(False)
        with patch('ui.widgets.uds_field_write_form.datetime') as clock:
            clock.now.return_value = datetime(2026,10,19,23,59,59)
            form.run_capture()
        assert pending
        assert not form.literal.isEnabled() and not form.sample.isEnabled()
        assert not dialog.can_physical_channel.isEnabled() and not dialog.device_combo.isEnabled()
        assert form.stop.isEnabled()
        assert 'AB2610192345' in form.preview.toPlainText()
        form.literal.setText('CHANGED')
        form.sample.setText('987654321')
        value = pending['action']()
        assert value == (True,'6E')
        assert client.request.call_args.args[1][2:] == b'AB2610192345'
        pending['done'](True,value)
        assert form.literal.isEnabled() and form.sample.isEnabled()
        assert not form.stop.isEnabled() and not dialog.btn_ok.isEnabled()
    finally:
        dialog.close()


def test_capture_refuses_timing_mismatch_and_recovers_when_task_rejected():
    from ui.dialogs.step_dialog import StepDialog
    driver,client = setup_driver()
    dialog = StepDialog(device_manager=SimpleNamespace(pcan=driver))
    form = dialog.uds_field_write_form
    try:
        form.load(config());form.sample.setText('123456789')
        dialog.recipe_can_binding = dict(channel='PCAN_USBBUS1',nominal_bitrate=250000)
        with patch.object(QMessageBox,'warning') as warning:
            form.run_capture()
            assert warning.called
            client.request.assert_not_called()
        dialog.recipe_can_binding = None
        form.task.run = lambda *args:False
        form.run_capture()
        assert form.capture.isEnabled() and form.sample.isEnabled() and not form.stop.isEnabled()
        client.request.assert_not_called()
    finally:
        dialog.close()
