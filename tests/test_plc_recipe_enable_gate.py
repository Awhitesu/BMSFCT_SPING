from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QMessageBox
from ui.tabs.overview_tab import OverviewTab

app = QApplication.instance() or QApplication([])


@pytest.fixture
def page(tmp_path, monkeypatch):
    state = {'enabled': False}
    plc = Mock()
    engine = Mock()
    engine.workers = {}
    engine.device_manager = SimpleNamespace(easy320=plc, is_enabled=lambda key:state['enabled'])
    db = Mock()
    db.load_sys_config.return_value = {}
    db.get_recipe_dir.return_value = str(tmp_path)
    widget = OverviewTab(engine, db)
    widget.stop_monitor()
    widget._refresh_recipe_for_new_test = Mock(return_value=True)
    widget.serialize_tree_to_dict = Mock(return_value={'name':'software', 'items':[{'name':'delay'}]})
    widget._confirmed_barcode = 'SN'
    widget.txt_sn.setText('SN')
    monkeypatch.setattr(QMessageBox, 'warning', Mock())
    monkeypatch.setattr(QMessageBox, 'critical', Mock())
    monkeypatch.setattr(widget._prepare_task, 'run', Mock(return_value=True))
    monkeypatch.setattr(widget._plc_task, 'run', Mock(return_value=True))
    yield widget, state, plc
    widget.begin_shutdown()
    for name in ('_prepare_task','_plc_task','_stats_task','_recipe_match_task'):
        getattr(widget, name).shutdown()
    widget.close()


@pytest.mark.parametrize('pending,error', [(7,''), (None,'PLC connection failed'),
                                          (7,'PLC recipe confirmation failed')])
def test_disabled_plc_does_not_block_start_on_pending_or_stale_failure(page, pending, error):
    widget, _, plc = page
    widget._pending_plc_recipe, widget._plc_config_error = pending, error
    assert widget.start_single_channel_test()
    widget._prepare_task.run.assert_called_once()
    QMessageBox.warning.assert_not_called()
    assert widget._pending_plc_recipe is None and widget._plc_config_error == ''
    plc.connect.assert_not_called()
    plc.write_register.assert_not_called()
    plc.read_register.assert_not_called()


@pytest.mark.parametrize('pending,error', [(7,''), (None,'PLC recipe confirmation failed')])
def test_enabled_plc_still_blocks_unconfirmed_recipe(page, pending, error):
    widget, state, _ = page
    state['enabled'] = True
    widget._pending_plc_recipe, widget._plc_config_error = pending, error
    assert not widget.start_single_channel_test()
    assert QMessageBox.warning.call_args.args[2] == 'PLC recipe confirmation is pending'
    widget._prepare_task.run.assert_not_called()


def test_disabled_selection_clears_old_error_without_scheduling_plc_work(page):
    widget, _, plc = page
    widget._plc_config_error = 'old failure'
    widget.sync_recipe_number_to_plc(8)
    assert widget._pending_plc_recipe is None and widget._plc_config_error == ''
    widget._plc_task.run.assert_not_called()
    plc.write_register.assert_not_called()


def test_late_confirmation_failure_is_ignored_after_plc_is_disabled(page):
    widget, state, _ = page
    state['enabled'] = True
    widget.sync_recipe_number_to_plc(8)
    _, completed = widget._plc_task.run.call_args.args
    state['enabled'] = False
    completed(False, 'PLC connection failed')
    assert widget._pending_plc_recipe is None and widget._plc_config_error == ''
    assert widget.lbl_big_status.text() != 'PLC ERROR'
    assert widget.start_single_channel_test()


def test_reenabled_plc_still_writes_and_verifies_before_start(page):
    widget, state, plc = page
    widget.sync_recipe_number_to_plc(8)
    state['enabled'] = True
    widget.sync_recipe_number_to_plc(9)
    write, completed = widget._plc_task.run.call_args.args
    assert not widget.start_single_channel_test()
    plc.write_register.return_value = True
    plc.read_register.return_value = [9]
    assert write() == 9
    plc.write_register.assert_called_once_with(0, 9, verify=False)
    plc.read_register.assert_called_once_with(address=0, count=1)
    completed(True, 9)
    assert widget.start_single_channel_test()
