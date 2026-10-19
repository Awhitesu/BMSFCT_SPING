from unittest.mock import Mock
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from core.read_until import poll, attach, settings
from ui.widgets.read_until_form import ReadUntilForm
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication([])


def config(**kwargs):
    return dict(dict(min=11.95,max=12.05,timeout=10,interval=.1,consecutive=1),**kwargs)


class Clock:
    now = 0
    def time(self): return self.now
    def wait(self,delay): self.now += delay; return False


def test_early_pass_and_stability_reset():
    clock = Clock(); values = iter([11,12,13,12,12]); log=[]
    assert poll(lambda:(True,next(values)),config(consecutive=2),lambda:False,clock.wait,log.append,clock.time) == (True,12)
    assert clock.now == pytest.approx(.4)
    assert len(log) == 5


def test_timeout_does_not_perform_extra_read():
    clock = Clock(); read = Mock(return_value=(True,11))
    with pytest.raises(RuntimeError,match='超时'):
        poll(read,config(timeout=.25),lambda:False,clock.wait,lambda _:None,clock.time)
    assert read.call_count == 3
    assert clock.now == .25


def test_cancel_communication_failure_and_late_result():
    clock = Clock()
    with pytest.raises(RuntimeError,match='取消'):
        poll(lambda:(True,11),config(),lambda:False,lambda _:True,lambda _:None,clock.time)
    with pytest.raises(RuntimeError,match='失败'):
        poll(lambda:(False,'offline'),config(),lambda:False,clock.wait,lambda _:None,clock.time)
    def slow(): clock.now=20;return True,12
    with pytest.raises(RuntimeError,match='超时'):
        poll(slow,config(),lambda:False,clock.wait,lambda _:None,clock.time)


def test_encoding_and_invalid_rules():
    for raw in ('读取电压 / UNIT:V', '设备工步:{"schema_version":2}'):
        assert settings(attach(raw,config())) == config()
    for invalid in (config(min=14),config(interval=0),config(max=float('nan')),config(consecutive=0)):
        with pytest.raises(ValueError): attach('读取电压',invalid)


def test_form_requires_limits_and_editor_roundtrip():
    form=ReadUntilForm(); form.show(); form.setChecked(True)
    with pytest.raises(ValueError): form.config()
    dialog=StepDialog()
    try:
        dialog.category_combo.setCurrentText('设备操作')
        dialog.sub_category_combo.setCurrentText('直流源')
        dialog.device_combo.setCurrentText('IT6722A 被测物供电电源 (IT6722A DUT Power)')
        dialog.action_combo.setCurrentText('回读数据')
        dialog.param_stack.setCurrentIndex(3)
        dialog.read_until.show();dialog.read_until.load(config())
        saved=dialog.get_data()
        assert settings(saved['params']) == config()
        restored=StepDialog(step_data=saved)
        assert restored.read_until.lower.text() == '11.95'
        assert restored.read_until.isChecked()
        restored.close()
    finally: dialog.close();form.close()


def test_engine_poll_collects_only_final_result_and_converts_units():
    from core.engine import ChannelWorker, SubStep, SubStepType
    driver=Mock(); driver.measure_voltage.side_effect=[11,12]
    worker=ChannelWorker(1,[])
    worker.device_manager=SimpleNamespace(dut_power=driver)
    worker.is_running=True
    sub=SubStep(SubStepType.READ_INSTRUMENT,dict(device='IT6722A DUT Power',params=attach('读取电压 / UNIT:V',config(interval=.01)),is_judgment=True))
    assert worker._execute_sub_step_logic(sub) == (True,12)
    assert worker.current_step_results == [(12,True)]
    assert driver.measure_voltage.call_count == 2
    worker.is_running=False


def test_it6722_current_poll_uses_current_and_milliamps():
    from core.engine import ChannelWorker, SubStep, SubStepType
    from core.measurement_units import is_voltage_read
    driver=Mock(); driver.measure_current.side_effect=[.05,.12]
    worker=ChannelWorker(1,[])
    worker.device_manager=SimpleNamespace(dut_power=driver)
    worker.is_running=True
    params=attach('读取电流 / UNIT:mA',config(min=100,max=150,interval=.01))
    # The wait configuration contains V after base64 encoding, independently
    # of the requested electrical quantity.
    assert 'V' in params.split('RW:')[1].upper()
    assert not is_voltage_read(params)
    sub=SubStep(SubStepType.READ_INSTRUMENT,dict(device='IT6722A DUT Power',params=params,is_judgment=True))
    try:
        ok,value=worker._execute_sub_step_logic(sub)
        assert ok and value == pytest.approx(120)
        assert value.unit == 'mA'
        assert driver.measure_current.call_count == 2
        driver.measure_voltage.assert_not_called()
        assert worker.current_step_results == [(value,True)]
    finally:
        worker.is_running=False
