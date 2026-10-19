import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from core.relay_combination import encode, decode, execute, ACTION
from core.engine import ChannelWorker, SubStep, SubStepType
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication([])


def manager():
    return SimpleNamespace(db_manager=SimpleNamespace(load_sys_config=lambda: {
        'easy320_channels':[dict(do=f'DO{i:o}',name='KL30' if i==13 else '') for i in range(24)]}),
        easy320=Mock(write_relay=Mock(return_value=True)))


def test_mixed_editor_roundtrip_and_inactive_points_untouched():
    mgr=manager();page=StepDialog(device_manager=mgr)
    try:
        page.category_combo.setCurrentText('设备操作')
        page.sub_category_combo.setCurrentText('继电器')
        page.device_combo.setCurrentText('Easy320 继电器 (Easy320)')
        page.action_combo.setCurrentText(ACTION)
        form=page.relay_combination_form
        assert len(form.cards)==24 and all(not c.isChecked() for c in form.checks)
        form.cards[13][0].setChecked(True);form.cards[13][1].click()
        form.cards[8][0].setChecked(True);form.cards[8][2].click()
        saved=page.get_data();targets=decode(saved['params'])
        assert targets==[dict(index=8,on=False),dict(index=13,on=True)]
        restored=StepDialog(step_data=saved,device_manager=mgr)
        try:assert restored.relay_combination_form.config()==targets
        finally:restored.close()
        worker=ChannelWorker(1,[],device_manager=mgr)
        ok,value=worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,saved))
        assert ok,value
        assert [c.args for c in mgr.easy320.write_relay.call_args_list]==[(8,False),(13,True)]
    finally:page.close()


def test_explicit_state_required_and_clear_means_no_operations():
    from ui.widgets.relay_combination_form import RelayCombinationForm
    form=RelayCombinationForm([dict(index=0,do='DO0',name='')])
    try:
        form.checks[0].setChecked(True)
        with pytest.raises(ValueError):form.config()
        form.cards[0][1].click()
        assert form.config()==[dict(index=0,on=True)]
        form.select_all(False)
        with pytest.raises(ValueError):form.config()
        with pytest.raises(ValueError):form.load([dict(index=1,on=True)])
    finally:form.close()


def test_validate_all_points_before_write_and_stop_after_failure():
    driver=Mock();allowed={i:dict(name='') for i in range(3)}
    with pytest.raises(ValueError):execute([dict(index=0,on=False),dict(index=9,on=True)],driver,allowed,lambda:False,print)
    driver.write_relay.assert_not_called()
    driver.write_relay.side_effect=[True,False]
    assert not execute([dict(index=0,on=True),dict(index=1,on=False),dict(index=2,on=True)],driver,allowed,lambda:False,lambda _:None)
    assert [c.args for c in driver.write_relay.call_args_list]==[(1,False),(0,True)]
    with pytest.raises(ValueError):encode([dict(index=1,on=True),dict(index=1,on=False)])


def test_old_close_step_can_switch_off_directly_and_save_mixed_targets():
    mgr=manager()
    page=StepDialog(device_manager=mgr, step_data=dict(device='Easy320 继电器 (Easy320)',
        type='继电器控制',action='闭合勾选通道',params='9,14',is_judgment=False))
    try:
        form=page.relay_combination_form
        check,on,off,_,_=form.cards[8]
        assert check.isChecked() and on.isChecked()
        assert on.isEnabled() and off.isEnabled()
        off.click()
        assert page.action_combo.currentText()==ACTION
        assert off.isChecked() and not on.isChecked()
        assert form.cards[13][1].isChecked()
        saved=page.get_data()
        assert decode(saved['params'])==[dict(index=8,on=False),dict(index=13,on=True)]
        on.click()
        assert on.isChecked() and not off.isChecked()
        check.setChecked(False)
        assert not on.isEnabled() and not off.isEnabled()
    finally:page.close()
