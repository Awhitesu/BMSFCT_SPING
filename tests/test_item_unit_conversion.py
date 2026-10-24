"""测试项使用目标单位判定和记录，采集证据保持来源值。"""
import json
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication

from core.engine import ChannelWorker, TestStep as ParentStep, StepType, NGStrategy, SubStep, SubStepType
from core.measurement_units import MeasurementValue, convert_result
from ui.tabs.overview_tab import OverviewTab


app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('value,source,target,expected', [
    (1, 'mA', 'uA', 1000), (.1, 'mA', 'uA', 100), (-.1, 'mA', 'μA', -100),
    (100, 'µA', 'mA', .1), (1000, 'uA', 'A', .001),
    (3.2, 'V', 'mV', 3200), (500, 'mV', 'V', .5), (.5, 'kV', 'V', 500),
    (1000, 'Ω', 'kΩ', 1), (1, 'MΩ', 'Ω', 1e6), (1, 'ohm', 'Ω', 1),
    (1e6, 'Hz', 'MHz', 1), (1, 'kHz', 'Hz', 1000),
    (.001, 's', 'ms', 1), (1, 'ms', 'us', 1000), (1, 'µs', 'μs', 1),
    (25, '℃', '°C', 25), (50, '%RH', '%', 50),
])
def test_standard_ratios_and_aliases(value, source, target, expected):
    raw = MeasurementValue(value, source)
    result = convert_result(raw, target)
    assert result == expected
    assert result.unit == target
    assert raw == value and raw.unit == source


@pytest.mark.parametrize('text,target,expected', [('1mA', 'uA', 1000), ('1e-3 A', 'uA', 1000), ('2.5 kΩ', 'Ω', 2500)])
def test_explicit_unit_suffix_can_supply_source(text, target, expected):
    result = convert_result(text, target)
    assert result == expected and result.unit == target


@pytest.mark.parametrize('value,target', [(1, 'uA'), ('PASS', 'uA'), ('001234', 'uA'),
                                          (MeasurementValue(1, 'mA'), 'NULL'),
                                          (MeasurementValue(1, 'mA'), '')])
def test_missing_source_or_target_does_not_guess(value, target):
    assert convert_result(value, target) is value


def worker_for(raw, unit, lower=None, upper=None, standard='数值'):
    step = ParentStep('measurement', StepType.CUSTOM, NGStrategy.CONTINUE_ON_NG)
    step.unit, step.min_limit, step.max_limit = unit, lower, upper
    step.standard_type = standard
    db = Mock()
    worker = ChannelWorker(1, [step], device_manager=SimpleNamespace(), db_manager=db)
    worker.is_running, worker.test_id = True, 7
    worker.current_step_results = [(raw, True)]
    return worker, step, db


def complete(worker):
    results = []
    worker.step_finished.connect(lambda *args: results.append(args))
    with patch('core.engine.QTimer.singleShot'):
        worker.on_step_complete()
    return results[0]


@pytest.mark.parametrize('raw,limits,expected,passed', [
    (1, (900, 1100), 1000, True), (.1, (90, 110), 100, True),
    (.1299, (0, 129), 129.9, False),
])
def test_parent_limits_database_and_cache_use_target_without_changing_raw(raw, limits, expected, passed):
    source = MeasurementValue(raw, 'mA')
    worker, step, db = worker_for(source, 'uA', *limits)
    step.decimal_places = 0
    event = complete(worker)
    assert event[-2:] == (passed, expected)
    assert event[-1].unit == 'uA' and step.unit == 'uA'
    assert db.log_item_result.call_args.args[4:6] == (expected, 'PASS' if passed else 'NG')
    assert db.log_item_result.call_args.kwargs['unit'] == 'uA'
    assert worker.step_measured_values[(0, step.name)] == expected
    assert worker.current_step_results[0][0] is source and source.unit == 'mA'


def test_incompatible_dimensions_fail_without_recording_a_mislabelled_number():
    source = MeasurementValue(1, 'V')
    worker, step, db = worker_for(source, 'uA', 0, 2)
    event = complete(worker)
    assert event[-2] is False and '不能将 V 换算为 uA' in event[-1]
    assert db.log_item_result.call_args.args[4].startswith('FAIL:')
    assert source.unit == 'V' and step.unit == 'uA'


@pytest.mark.parametrize('unit', ['NULL', ''])
def test_unconfigured_item_unit_retains_native_unit(unit):
    worker, step, db = worker_for(MeasurementValue(1, 'mA'), unit, 0, 2)
    assert complete(worker)[-2:] == (True, 1)
    assert step.unit == db.log_item_result.call_args.kwargs['unit'] == 'mA'


@pytest.mark.parametrize('standard', ['字符串', '变量'])
def test_identifier_and_variable_judgments_keep_original_value(standard):
    raw = MeasurementValue(1, 'mA')
    worker, step, _ = worker_for(raw, 'uA', '1.0', standard=standard)
    if standard == '变量':
        step.min_limit = 'expected'
        worker.variables['expected'] = 1.0
    assert complete(worker)[-1] is raw


@pytest.mark.parametrize('device,value,unit', [('IT6722A DUT Power', .001, 'A'), ('电池模拟器', 1, 'mA')])
def test_legacy_instrument_read_keeps_source_unit_without_unit_parameter(device, value, unit):
    worker, _, _ = worker_for(None, 'uA', 900, 1100)
    worker.current_step_results.clear()
    worker.device_manager = SimpleNamespace(dut_power=Mock(), measure_current=Mock(return_value=value))
    worker.device_manager.dut_power.measure_current.return_value = value
    sub = SubStep(SubStepType.READ_INSTRUMENT, dict(device=device, params='读取电流', is_judgment=True))
    ok, result = worker._execute_sub_step_logic(sub)
    assert ok and result == value and result.unit == unit
    assert complete(worker)[-2:] == (True, 1000)


def test_legacy_variable_read_collects_measurement_without_losing_unit_or_precision():
    source = MeasurementValue(.00123, 'mA')
    worker, _, _ = worker_for(None, 'uA', 1, 2)
    worker.current_step_results.clear()
    worker.variables['current'] = source
    sub = SubStep(SubStepType.READ_INSTRUMENT,
                  dict(device='变量操作', params='VAR:current', is_judgment=True))
    assert worker._execute_sub_step_logic(sub) == (True, source)
    assert worker.current_step_results == [(source, True)]
    assert complete(worker)[-2:] == (True, 1.23)


def test_dbc_snapshot_retains_native_unit_and_choice_output_stays_text():
    from core.dbc_signals import execute_snapshot
    key = ('CAN1',)
    snapshot = dict(name='sample', values={key: dict(value=3.2, unit='V', choice='ON', age=0)})
    config = dict(message='Status', signal='Voltage')
    ok, result = execute_snapshot(snapshot, config, key, lambda: False, lambda _: None)
    assert ok and result.unit == 'V' and convert_result(result, 'mV') == 3200
    assert execute_snapshot(snapshot, dict(config, output='choice'), key, lambda: False, lambda _: None) == (True, 'ON')


@pytest.mark.parametrize('places,expected', [(None, '33.6643'), (0, '33'), (2, '33.66')])
def test_microamp_display_converts_before_truncation_and_keeps_native_evidence(tmp_path, places, expected):
    from ui.dialogs.monitor_dialog import MonitorDialog
    source = MeasurementValue(.0336643, 'mA')
    worker, step, _ = worker_for(None, 'uA', 30, 40)
    step.decimal_places = places
    worker.current_step_results.clear()
    sub_data = dict(type='读取变量', params='VAR:current', is_judgment=True)
    path = tmp_path / 'units.json'
    path.write_text(json.dumps(dict(name='units', items=[dict(name=step.name, min=30, max=40,
                    unit='uA', decimal_places=places, sub_steps=[sub_data])])), encoding='utf-8')
    page = OverviewTab()
    page.stop_monitor()
    try:
        assert page.load_recipe_from_file(str(path))
        worker.variables['current'] = source
        worker.sub_step_finished.connect(page.on_worker_sub_step_finished)
        worker.step_finished.connect(page.on_worker_step_finished)
        sub = SubStep(SubStepType.READ_VAR, sub_data)
        step.add_sub_step(sub)
        worker._execute_sub_step_logic(sub)
        complete(worker)
        row = page.step_tree.topLevelItem(0)
        assert row.text(4) == row.child(0).text(4) == expected
        assert row.text(5) == row.child(0).text(5) == 'uA'
        assert worker.sub_step_statuses[(0, 0)][1] is source
        assert source == .0336643 and source.unit == 'mA'
        monitor = MonitorDialog(engine=SimpleNamespace(workers={1: worker}))
        try:
            row = monitor.step_tree.topLevelItem(0)
            assert row.text(3) == row.child(0).text(3) == expected
        finally:
            monitor.close()
            monitor.deleteLater()
    finally:
        page.begin_shutdown()
        for name in ('_prepare_task', '_plc_task', '_stats_task', '_recipe_match_task'):
            getattr(page, name).shutdown()
        page.close()
        page.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.DeferredDelete)


@pytest.mark.parametrize('unit', ['nA', 'µV', 'μΩ', 'GHz', 'ns', '%RH'])
def test_explicit_same_unit_text_is_accepted_by_numeric_judgment(unit):
    worker, _, _ = worker_for('1' + unit, unit, 0, 2)
    assert complete(worker)[-2] is True


def test_database_and_real_xls_writer_receive_matching_value_and_unit(tmp_path):
    from data.db_manager import DBManager
    from core.excel_recorder import export_test_record
    from core.xls_report import build_workbook
    from core.result_display import format_result

    worker, step, _ = worker_for(MeasurementValue(.123456, 'mA'), 'uA', 100, 200)
    db = DBManager(str(tmp_path / 'units.db'))
    try:
        recipe = dict(name='units', items=[dict(name=step.name, step_id=step.step_id,
                      unit='uA', decimal_places=2)])
        worker.db_manager = db
        worker.test_id = db.start_new_test(1, 'CH1', 'SN', 'units', context={'recipe': recipe})
        assert complete(worker)[-2:] == (True, 123.456)
        db.finish_test(worker.test_id, 'PASS')
        with patch('core.excel_recorder.build_workbook', wraps=build_workbook) as render:
            report = export_test_record(db.db_path, worker.test_id, str(tmp_path / 'reports'))
        rows = render.call_args.args[1]
        assert rows[0]['unit'] == 'uA' and rows[0]['measured_value'] == '123.456'
        assert format_result(rows[0]['measured_value'], rows[0]) == '123.45'
        assert Path(report).read_bytes()[:8] == bytes.fromhex('D0 CF 11 E0 A1 B1 1A E1')
    finally:
        db.close()
