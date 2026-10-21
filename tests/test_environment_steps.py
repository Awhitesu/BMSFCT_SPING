from datetime import datetime
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QWidget, QTreeWidget, QTreeWidgetItem

from core.device_steps import DEVICES, encode, execute
from core.engine import ChannelWorker, SubStep, SubStepType, TestStep as RecipeItem, StepType
from core.read_until import attach
from devices.renke_upload import RenkeUploadSensor
from devices.rs_ws_eth6 import EnvironmentReading
from ui.dialogs.step_dialog import StepDialog
from ui.widgets.variable_step_form import catalog


app = QApplication.instance() or QApplication([])


def reading(temperature=-12.3, humidity=54.8):
    return EnvironmentReading(temperature, humidity, datetime.now(), 3, (548, 0x807b),
                              '仁科 P2 主动上传', '10117851', 1)


def manager_for(driver):
    return SimpleNamespace(rs_ws_eth6=driver, is_enabled=lambda _: True,
                           device_step_lock=lambda _: threading.RLock())


def sensor_driver():
    driver = SimpleNamespace(_lock=threading.RLock(), is_connected=True, last_error='',
                             connect=Mock(return_value=True), read_environment=Mock(return_value=reading()))
    return driver


@pytest.mark.parametrize('action,expected,unit', [('temperature',-12.3,'℃'),('humidity',54.8,'%RH')])
def test_read_step_output_units_variable_and_time_log(action, expected, unit):
    driver = sensor_driver()
    values, logs = {}, []
    ok, value = execute(manager_for(driver), encode('rs_ws_eth6', action, {'variable':'ENV'}), values, logs.append)
    assert ok and value == expected and value.unit == unit
    assert values['ENV'] == expected and values['ENV'].unit == unit
    driver.read_environment.assert_called_once()
    driver.connect.assert_not_called()
    assert any('10117851' in line for line in logs)


@pytest.mark.parametrize('error', [ConnectionError('数据超时'), TimeoutError('offline')])
def test_failure_invalidates_previous_named_variable(error):
    driver = sensor_driver()
    driver.read_environment.side_effect = error
    values = {'ENV':99,'other':'keep'}
    with pytest.raises(type(error)):
        execute(manager_for(driver),encode('rs_ws_eth6','temperature',{'variable':'ENV'}),values,lambda _:None)
    assert values == {'other':'keep'}


def test_missing_upload_value_fails_without_reopening_running_listener():
    driver = RenkeUploadSensor('127.0.0.1', 2404)
    driver.connect = Mock(return_value=True)
    values = {'ENV':42}
    with pytest.raises(ConnectionError, match='尚未收到'):
        execute(manager_for(driver), encode('rs_ws_eth6','temperature',{'variable':'ENV'}), values, lambda _:None)
    assert 'ENV' not in values


def test_disabled_sensor_and_cancellation_never_read():
    driver = sensor_driver()
    manager = manager_for(driver)
    manager.is_enabled = lambda _: False
    with pytest.raises(ValueError,match='未启用'):
        execute(manager,encode('rs_ws_eth6','humidity',{}),{},lambda _:None)
    driver.read_environment.assert_not_called()
    with pytest.raises(RuntimeError,match='已停止'):
        execute(manager_for(driver),encode('rs_ws_eth6','humidity',{}),{},lambda _:None,cancelled=lambda:True)
    driver.read_environment.assert_not_called()


def test_cancellation_during_read_cannot_publish_variable():
    driver = sensor_driver()
    stop = threading.Event()
    driver.read_environment.side_effect = lambda: (stop.set() or reading())
    values = {'ENV':10}
    with pytest.raises(RuntimeError,match='已停止'):
        execute(manager_for(driver),encode('rs_ws_eth6','temperature',{'variable':'ENV'}),values,lambda _:None,cancelled=stop.is_set)
    assert 'ENV' not in values


def test_nonfinite_measurement_is_rejected():
    driver = sensor_driver()
    driver.read_environment.return_value = reading(temperature=float('nan'))
    with pytest.raises(ValueError,match='无效'):
        execute(manager_for(driver),encode('rs_ws_eth6','temperature',{}),{},lambda _:None)


@pytest.mark.parametrize('action', ['temperature','humidity'])
def test_editor_roundtrip_and_range_wait(action):
    data = dict(device=DEVICES['rs_ws_eth6'][1], action='读取温度' if action=='temperature' else '读取湿度',
                params=encode('rs_ws_eth6',action,{'variable':'环境温度' if action=='temperature' else '环境湿度'}),
                type='读取仪表',is_judgment=True,fail_strategy='失败停止')
    dialog = StepDialog(step_data=data)
    restored = None
    try:
        assert dialog.category_combo.currentText() == '设备操作'
        assert dialog.sub_category_combo.currentText() == '测量仪器'
        assert dialog.param_stack.currentWidget() is dialog.device_step_form
        assert set(dialog.device_step_form.controls) == {'variable'}
        limits = dict(min=15,max=35,timeout=20,interval=1,consecutive=1)
        dialog.read_until.load(limits)
        saved = dialog.get_data()
        restored = StepDialog(step_data=saved)
        assert restored.get_data() == saved
        assert saved['type'] == '读取仪表' and saved['is_judgment'] is True
        assert restored.read_until.isChecked()
    finally:
        dialog.close()
        if restored:
            restored.close()


def test_saved_sensor_variable_is_in_read_and_judgment_catalog():
    parent = QWidget()
    parent.step_tree = QTreeWidget(parent)
    parent.step_tree.setColumnCount(3)
    item = QTreeWidgetItem(parent.step_tree, ['温度'])
    sub = QTreeWidgetItem(item, ['读取温度','',encode('rs_ws_eth6','temperature',{'variable':'环境温度'})])
    sub.setData(0,Qt.UserRole+20,'temp-step')
    try:
        sources, keys = catalog(parent)
        assert '环境温度' in keys
        assert sources[0][1] == 'temp-step'
    finally:
        parent.close()


def sub_step(action, **values):
    return SubStep(SubStepType.READ_INSTRUMENT, dict(device=DEVICES['rs_ws_eth6'][1],
        params=encode('rs_ws_eth6',action,values),is_judgment=True))


def test_engine_range_wait_stops_on_first_in_range_value():
    driver = sensor_driver()
    driver.read_environment.side_effect = [reading(40),reading(22.7)]
    worker = ChannelWorker(1,[],device_manager=manager_for(driver))
    worker.is_running = True
    step = sub_step('temperature',variable='环境温度')
    step.params['params'] = attach(step.params['params'],dict(min=15,max=35,timeout=2,interval=.01,consecutive=1))
    try:
        ok, value = worker._execute_sub_step_logic(step)
        assert ok and value == 22.7 and value.unit == '℃'
        assert driver.read_environment.call_count == 2
        assert worker.current_step_results == [(value,True)]
        assert worker.variables['环境温度'] == 22.7
    finally:
        worker.is_running = False


@pytest.mark.parametrize('value,expected', [(22.7,True),(40,False)])
def test_engine_parent_limits_and_report_unit(value,expected):
    driver = sensor_driver()
    driver.read_environment.return_value = reading(value)
    item = RecipeItem('环境温度',StepType.CUSTOM)
    item.min_limit, item.max_limit = 15, 35
    db = Mock()
    worker = ChannelWorker(1,[item],device_manager=manager_for(driver),db_manager=db)
    worker.is_running = True
    worker.test_id = 7
    worker._perform_stop = Mock()
    results = []
    worker.step_finished.connect(lambda *args: results.append(args))
    try:
        assert worker._execute_sub_step_logic(sub_step('temperature'))[0]
        worker.on_step_complete()
        assert results[-1][3] is expected
        assert item.unit == '℃'
        assert db.log_item_result.call_args.kwargs['unit'] == '℃'
    finally:
        worker.is_running = False
