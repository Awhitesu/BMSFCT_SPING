from types import SimpleNamespace
from unittest.mock import Mock

from PySide6.QtWidgets import QApplication

from core.engine import ChannelWorker, SubStep, SubStepType
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication([])


def manager():
    db = Mock()
    db.load_sys_config.return_value = {'easy320_channels': [
        {'do':'DO10', 'name':'连接器连接'}, {'do':'DO15', 'name':'KL30'},
        {'do':'DO27', 'name':''}]}
    return SimpleNamespace(db_manager=db, easy320=Mock())


def test_step_uses_imported_labels_and_actual_octal_offsets():
    mgr = manager()
    dialog = StepDialog(device_manager=mgr)
    try:
        assert [cb.text() for cb in dialog.easy320_checks] == ['DO10 (连接器连接)', 'DO15 (KL30)', 'DO27']
        assert [cb.property('coil_index') for cb in dialog.easy320_checks] == [8,13,23]
        dialog.category_combo.setCurrentText('设备操作')
        dialog.sub_category_combo.setCurrentText('继电器')
        dialog.device_combo.setCurrentText('Easy320 继电器 (Easy320)')
        dialog.action_combo.setCurrentText('闭合勾选通道')
        dialog.easy320_checks[1].setChecked(True)
        saved = dialog.get_data()
        assert saved['params'] == '14'
        restored = StepDialog(step_data=saved, device_manager=mgr)
        try:
            assert [cb.property('coil_index') for cb in restored.easy320_checks if cb.isChecked()] == [13]
        finally: restored.close()
    finally: dialog.close()


def test_execution_rejects_removed_point_before_any_write():
    mgr = manager()
    worker = ChannelWorker(0, [], device_manager=mgr)
    ok, _ = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device':'Easy320', 'action':'闭合勾选通道', 'params':'9,1'}))
    assert not ok
    mgr.easy320.write_relay.assert_not_called()


def test_execution_writes_real_offsets_and_bulk_only_imported_points():
    mgr = manager(); mgr.easy320.write_relay.return_value=True
    worker = ChannelWorker(0, [], device_manager=mgr)
    ok, _ = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device':'Easy320', 'action':'闭合勾选通道', 'params':'14'}))
    assert ok
    mgr.easy320.write_relay.assert_called_once_with(13,True)
    mgr.easy320.reset_mock()
    ok, _ = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        {'device':'Easy320', 'action':'全部断开', 'params':''}))
    assert ok
    assert [c.args for c in mgr.easy320.write_relay.call_args_list] == [(8,False),(13,False),(23,False)]
    mgr.easy320.batch_control.assert_not_called()
