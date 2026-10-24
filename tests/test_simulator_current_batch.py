from collections import Counter
from types import SimpleNamespace
from unittest.mock import Mock
import threading

import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication

from core.device_steps import DEVICES, encode, decode, execute
from core.read_until import attach, settings
from core.variable_steps import encode as encode_variable
from devices.manager import DeviceManager
from devices.ngi_83624 import NGI83624
from devices.simulator_config import channel_routes
from ui.dialogs.step_dialog import StepDialog
from ui.tabs.simulator_tab import SimulatorTab

app = QApplication.instance() or QApplication([])


class Simulator:
    def __init__(self, number, count):
        self.sequence_number, self.max_channels = number, count
        self.calls = Counter()

    def read_current_sample(self, channel, cancelled=lambda:False):
        self.calls[channel] += 1
        # Different channels and samples must remain separate through aggregation.
        return self.sequence_number * 100 + channel + self.calls[channel] / 10


def manager(counts=(24, 18, 14)):
    result = DeviceManager.__new__(DeviceManager)
    result.simulators = [Simulator(number, count) for number, count in enumerate(counts, 1)]
    result.enabled_devices = {f'sim{slot}': True for slot in range(1, len(counts) + 1)}
    result.is_virtual = False
    return result


def batch(mgr, variables=None, **overrides):
    config = dict(selection='ALL', count=3, mode='mean')
    config.update(overrides)
    return execute(mgr, encode('simulator', 'current_batch', config),
                   variables if variables is not None else {}, lambda _:None)


def test_continuous_mapping_respects_number_order_partial_and_disabled_devices():
    mgr = manager()
    assert [ch for ch, _, _ in mgr.simulator_channels()] == list(range(1, 57))
    assert mgr._get_sim_and_ch(42) == (mgr.simulators[1], 18)
    assert mgr._get_sim_and_ch(43) == (mgr.simulators[2], 1)
    assert mgr._get_sim_and_ch(56) == (mgr.simulators[2], 14)
    mgr.enabled_devices['sim2'] = False
    assert mgr._get_sim_and_ch(25) == (mgr.simulators[2], 1)
    assert len(mgr.simulator_channels()) == 38
    mgr.simulators[0].sequence_number = 6
    assert mgr._get_sim_and_ch(1) == (mgr.simulators[2], 1)
    assert mgr._get_sim_and_ch(15) == (mgr.simulators[0], 1)
    with pytest.raises(ValueError): channel_routes([(1, 1, 'a'), (1, 2, 'b')])


@pytest.mark.parametrize('selection,parity', [('ALL', None), ('ODD', 1), ('EVEN', 0)])
@pytest.mark.parametrize('mode,increment', [('mean', .2), ('max', .3)])
def test_each_channel_collects_exact_count_and_calculates_its_own_value(selection, parity, mode, increment):
    mgr = manager()
    variables = {'other': 7}
    ok, summary = batch(mgr, variables, selection=selection, mode=mode)
    selected = [(ch, sim, local) for ch, sim, local in mgr.simulator_channels()
                if parity is None or ch % 2 == parity]
    assert ok and summary.startswith('PASS:')
    assert set(variables) == {'other'} | {f'电流-{ch}' for ch, _, _ in selected}
    for ch, sim, local in selected:
        assert sim.calls[local] == 3
        assert variables[f'电流-{ch}'] == pytest.approx(sim.sequence_number * 100 + local + increment)
        assert variables[f'电流-{ch}'].unit == 'mA'
    assert sum(sum(sim.calls.values()) for sim in mgr.simulators) == len(selected) * 3


def test_all_56_channels_get_100_samples_and_56_variables():
    mgr = manager()
    variables = {}
    batch(mgr, variables, count=100)
    assert len(variables) == 56
    assert all(count == 100 for sim in mgr.simulators for count in sim.calls.values())
    assert variables['电流-43'] == pytest.approx(306.05)
    assert variables['电流-56'] == pytest.approx(319.05)


@pytest.mark.parametrize('bad', [float('nan'), float('inf'), 9.9e37, None, True])
def test_failure_never_publishes_partial_values_or_reuses_selected_old_values(bad):
    mgr = manager((2,))
    read = mgr.simulators[0].read_current_sample
    mgr.simulators[0].read_current_sample = lambda ch, **kw: bad if ch == 2 else read(ch, **kw)
    variables = {'电流-1': 99, '电流-2': 99, 'other': 8}
    with pytest.raises(RuntimeError, match='全局通道2.*第1/3次'):
        batch(mgr, variables)
    assert variables == {'other': 8}


def test_signed_current_and_arithmetic_maximum():
    mgr = manager((1,))
    mgr.simulators[0].read_current_sample = Mock(side_effect=[-2, -1, -3])
    variables = {}
    batch(mgr, variables, mode='max')
    assert variables['电流-1'] == -1


def test_mean_retains_small_signed_samples_and_odd_means_global_channel_parity():
    mgr = manager((1, 2))
    mgr.simulators[0].read_current_sample = Mock(side_effect=[1e16, 1, -1e16])
    mgr.simulators[1].read_current_sample = Mock(return_value=2)
    variables = {}
    batch(mgr, variables, selection='ODD')
    assert variables['电流-1'] == pytest.approx(1 / 3)
    assert variables['电流-3'] == 2
    assert mgr.simulators[1].read_current_sample.call_args_list[0].args == (2,)
    assert '电流-2' not in variables


def test_batch_variable_keys_are_available_to_following_variable_steps():
    from PySide6.QtWidgets import QWidget, QTreeWidget, QTreeWidgetItem
    from ui.widgets.variable_step_form import catalog
    parent = QWidget()
    parent.device_manager = manager((1, 2))
    parent.step_tree = QTreeWidget(parent)
    parent.step_tree.setColumnCount(3)
    root = QTreeWidgetItem(parent.step_tree, ['test'])
    QTreeWidgetItem(root, ['batch', '', encode('simulator', 'current_batch',
                                           dict(selection='ODD', count=100, mode='mean'))])
    try:
        assert catalog(parent)[1] == {'电流-1', '电流-3'}
    finally: parent.close()


def test_odd_read_preserves_even_values_and_cancel_discards_all_pending_results():
    mgr = manager((2,))
    variables = {'电流-1': 88, '电流-2': 99}
    batch(mgr, variables, selection='ODD')
    assert variables['电流-2'] == 99
    stopped = threading.Event()
    def read(channel, **_):
        stopped.set()
        return 1
    mgr.simulators[0].read_current_sample = read
    with pytest.raises(RuntimeError, match='已停止'):
        execute(mgr, encode('simulator', 'current_batch', dict(selection='ALL', count=1, mode='mean')),
                variables, lambda _:None, stopped.is_set)
    assert variables == {}


def test_no_enabled_or_matching_channels_fails_before_any_query():
    mgr = manager((1,))
    with pytest.raises(ValueError, match='没有符合'): batch(mgr, selection='EVEN')
    mgr.enabled_devices['sim1'] = False
    with pytest.raises(ValueError, match='没有符合'): batch(mgr)
    assert not mgr.simulators[0].calls


@pytest.mark.parametrize('overrides', [dict(count=0), dict(count=True), dict(count=1.5),
                                     dict(selection='bad'), dict(mode='bad')])
def test_invalid_recipe_settings_are_rejected(overrides):
    with pytest.raises(ValueError): batch(manager((1,)), **overrides)


def test_driver_reads_one_fresh_sample_without_stability_wait_or_output_changes():
    driver = NGI83624('unused', max_channels=2)
    driver._query = Mock(return_value='-1.0')
    assert driver.read_current_sample(2) == -1
    driver._query.assert_called_once_with('MEAS2:CURR?', None)
    for reply in ('nan', 'inf', '9.9e37', 'invalid'):
        driver._query.return_value = reply
        with pytest.raises(ValueError): driver.read_current_sample(1)
    driver._query.side_effect = TimeoutError('query timeout')
    with pytest.raises(TimeoutError): driver.read_current_sample(1)
    driver._query.reset_mock()
    with pytest.raises(RuntimeError): driver.read_current_sample(1, cancelled=lambda:True)
    driver._query.assert_not_called()


def simulator_dialog(mgr=None):
    dialog = StepDialog(device_manager=mgr)
    dialog.sub_category_combo.setCurrentText('模拟电池')
    dialog.action_combo.setCurrentText('回读数据')
    return dialog


def test_new_editor_only_has_two_actions_and_roundtrips_three_parameters():
    dialog = simulator_dialog(manager())
    restored = None
    try:
        assert [dialog.action_combo.itemText(i) for i in range(dialog.action_combo.count())] == ['快捷配置', '回读数据']
        assert dialog.read_until.isHidden()
        assert dialog.param_stack.currentWidget() is dialog.simulator_read_form
        controls = dialog.simulator_read_form.fields.controls
        controls['selection'].setCurrentIndex(controls['selection'].findData('EVEN'))
        controls['count'].setValue(100)
        controls['mode'].setCurrentIndex(controls['mode'].findData('mean'))
        assert '28' in dialog.simulator_read_form.preview.text()
        saved = dialog.get_data()
        assert saved['type'] == '读取仪表' and saved['is_judgment'] is False
        assert settings(saved['params']) is None
        assert decode(saved['params'])[2] == dict(selection='EVEN', count=100, mode='mean')
        restored = StepDialog(step_data=saved, device_manager=manager())
        assert restored.get_data() == saved
        restored.action_combo.setCurrentText('快捷配置')
        assert restored.param_stack.currentIndex() == 6
        assert restored.get_data()['type'] == '设置仪表'
        # Other devices keep their voltage/current choices and range polling.
        restored.sub_category_combo.setCurrentText('直流源')
        restored.action_combo.setCurrentText('回读数据')
        assert restored.param_stack.currentIndex() == 3 and not restored.read_until.isHidden()
    finally:
        if restored: restored.close()
        dialog.close()


@pytest.mark.parametrize('action', ['current', 'voltage', 'on', 'all_off'])
def test_historical_typed_steps_preserve_parameters_and_explicit_conversion(action):
    params = encode('simulator', action, {'channel': 43, 'variable': 'old'} if action in ('current','voltage')
                    else {'channel': 43} if action == 'on' else {})
    if action in ('current', 'voltage'):
        params = attach(params, dict(min=1,max=5,timeout=10,interval=.1,consecutive=1))
    saved = dict(device=DEVICES['simulator'][1], action='old', params=params, name='old name',
                 type='读取仪表' if action in ('current','voltage') else '设置仪表',
                 is_judgment=True, fail_strategy='忽略继续')
    dialog = StepDialog(step_data=saved)
    try:
        assert dialog.btn_ok.isEnabled() and dialog.get_data() == saved
        assert dialog.action_combo.count() == 2 and dialog.read_until.isHidden()
        if action == 'current':
            dialog.device_step_form.controls['channel'].setValue(42)
            changed = dialog.get_data()
            assert decode(changed['params'])[2]['channel'] == 42
            assert settings(changed['params']) == settings(params)
        dialog.action_combo.setCurrentText('回读数据')
        dialog.action_combo.activated.emit(dialog.action_combo.currentIndex())
        assert decode(dialog.get_data()['params'])[1].key == 'current_batch'
        assert dialog.get_data()['is_judgment'] is False
    finally: dialog.close()


def test_legacy_text_read_is_not_silently_converted_to_all_channels():
    saved = dict(device=DEVICES['simulator'][1], action='回读数据', params='读取电流 / CH:43',
                 type='读取仪表', name='legacy', is_judgment=True, fail_strategy='失败停止')
    dialog = StepDialog(step_data=saved)
    try:
        assert dialog.btn_ok.isEnabled() and dialog.get_data() == saved
    finally: dialog.close()


def test_debug_configuration_follows_hardware_parameters_without_local_settings():
    db = Mock()
    db.load_sys_config.return_value = dict(sim1_channels=24, sim2_enabled=True, sim2_channels=18,
                                          sim3_enabled=True, sim3_channels=14)
    mgr = DeviceManager(db)
    page = SimulatorTab(mgr)
    def extent(index):
        channels=[ch for ch,sim,_ in page._routes if sim is mgr.simulators[index]]
        return (channels[0],channels[-1]) if channels else None
    try:
        assert [extent(row) for row in range(3)] == [(1,24), (25,42), (43,56)]
        assert not hasattr(page, 'settings_controls') and not hasattr(page, 'save_simulator_settings')
        cfg = db.load_sys_config.return_value
        cfg['sim2_enabled'] = False
        mgr.update_config()
        page.refresh_configuration()
        assert extent(1) is None
        assert extent(2) == (25,38)
        cfg['sim1_channels'] = 18
        mgr.update_config()
        page.refresh_configuration()
        assert extent(2) == (19,32)
        cfg.update(sim1_number=3, sim3_number=1)
        mgr.update_config()
        page.refresh_configuration()
        assert extent(2) == (1,14)
        assert extent(0) == (15,32)
        assert page.table.rowCount() == 32 and len(page.channel_checks) == 24
        db.save_sys_config.assert_not_called()
    finally:
        page.status_timer.stop()
        page.shutdown()
        page.close()


def test_engine_writes_variables_and_later_variable_read_keeps_unit_and_errors():
    from core.engine import ChannelWorker, SubStep, SubStepType
    mgr = manager((2,))
    mgr.hv_source = SimpleNamespace(last_error='unrelated high-voltage error')
    worker = ChannelWorker(1, [])
    worker.device_manager, worker.is_running = mgr, True
    step = SubStep(SubStepType.READ_INSTRUMENT, dict(device=DEVICES['simulator'][1],
        params=encode('simulator','current_batch',dict(selection='ALL', count=3, mode='mean')), is_judgment=False))
    ok, _ = worker._execute_sub_step_logic(step)
    assert ok and worker.variables['电流-2'] == pytest.approx(102.2)
    variable = SubStep(SubStepType.READ_VAR, dict(params=encode_variable(dict(
        operation='read', key='电流-2', input='text', segments=[])), is_judgment=True))
    ok, value = worker._execute_sub_step_logic(variable)
    assert ok and value.unit == 'mA' and value == pytest.approx(102.2)
    mgr.simulators[0].read_current_sample = Mock(side_effect=TimeoutError('current query timeout'))
    ok, reason = worker._execute_sub_step_logic(step)
    assert not ok and '全局通道1' in reason and 'current query timeout' in reason
    assert '电流-1' not in worker.variables and '电流-2' not in worker.variables
    worker.is_running = False
