"""三档量程的编辑、保存及固定限流规则；不连接实际设备。"""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import json
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QComboBox, QDoubleSpinBox

from core.device_steps import DEVICES
from core.simulator_control import simulator_configuration, configure_simulator, control_simulator
from devices.ngi_83624 import NGI83624
from ui.dialogs.step_dialog import StepDialog
from ui.tabs.simulator_tab import SimulatorTab
from test_simulator_combined_configuration import Instrument, writes
from test_simulator_live_confirmation import driver


app = QApplication.instance() or QApplication([])


@pytest.mark.parametrize('mode,current_ma', [('HIGH', 1000), ('LOW', 1), ('AUTO', 500)])
def test_all_three_ranges_save_and_reopen_with_native_codes(mode, current_ma):
    source = StepDialog()
    restored = None
    try:
        source.sub_category_combo.setCurrentText('模拟电池')
        source.action_combo.setCurrentText('快捷配置')
        assert [source.sim_batch_range.itemText(i) for i in range(3)] == ['0–1000 mA', '0–1 mA', 'AUTO']
        source.sim_batch_range.setCurrentIndex(source.sim_batch_range.findData(mode))
        source.sim_batch_curr.setValue(current_ma)
        source.sim_batch_channels.setText('2,7')
        saved = json.loads(json.dumps(source.get_data(), ensure_ascii=False))
        assert f'Range:{mode}' in saved['params']
        restored = StepDialog(step_data=saved)
        assert restored.sim_batch_range.currentData() == mode
        assert restored.sim_batch_curr.value() == current_ma
        assert restored.sim_batch_curr.isEnabled() is (mode != 'LOW')
        assert restored.get_data() == saved
    finally:
        if restored:
            restored.close()
        source.close()


def test_low_limit_is_fixed_in_editor_and_high_auto_restore_editing():
    page = StepDialog(step_data=dict(device=DEVICES['simulator'][1], action='快捷配置',
                                    params='3.2V / 0.5mA / ON / Range:LOW / CH:ALL', type='设置仪表'))
    try:
        assert page.sim_batch_curr.minimum() == page.sim_batch_curr.maximum() == 1
        page.sim_batch_curr.setValue(.5)
        assert page.sim_batch_curr.value() == 1
        assert '1.0mA' in page.get_data()['params']
        for mode in ('HIGH', 'AUTO'):
            page.sim_batch_range.setCurrentIndex(page.sim_batch_range.findData(mode))
            assert page.sim_batch_curr.isEnabled()
            assert page.sim_batch_curr.minimum() == 0 and page.sim_batch_curr.maximum() == 1000
    finally:
        page.close()


@pytest.mark.parametrize('current', [0, .0005, .0011, .01])
def test_explicit_nonfixed_low_limit_is_rejected_before_device_io(current):
    sim = Instrument()
    with pytest.raises(ValueError, match='1 mA'):
        configure_simulator(sim, [1], dict(range='LOW', current=current, output=True))
    assert sim.io == []


def test_low_without_explicit_limit_uses_fixed_one_ma():
    assert simulator_configuration(dict(range='LOW', output=True))['current'] == .001
    sim = Instrument(enabled=False)
    assert configure_simulator(sim, [1], dict(range='LOW', output=True))
    assert sim.data[1]['range'] == 2 and sim.data[1]['current'] == 1
    assert sim.data[2]['current'] == 1000


@pytest.mark.parametrize('mode', ['HIGH', 'AUTO'])
@pytest.mark.parametrize('current', [0, .0005, 1])
def test_high_and_auto_accept_limits_within_one_amp(mode, current):
    config = simulator_configuration(dict(range=mode, current=current, output=True))
    assert config['current'] == current


@pytest.mark.parametrize('mode', ['HIGH', 'AUTO'])
def test_high_and_auto_reject_over_one_amp_before_io(mode):
    sim = Instrument()
    with pytest.raises(ValueError, match='1000 mA'):
        configure_simulator(sim, [1], dict(range=mode, current=1.1, output=True))
    assert sim.io == []
    raw = NGI83624('unused')
    raw._safe_send = Mock()
    assert not raw.set_current_limit(1, 1.1)
    raw._safe_send.assert_not_called()


@pytest.mark.parametrize('current', [0, .0005, .01])
def test_standalone_low_limit_rejects_every_value_except_one_ma(current):
    sim = driver({1: True})
    sim.read_source_settings.return_value = {'range': 'LOW', 'current': .001}
    with pytest.raises(ValueError, match='1 mA'):
        control_simulator(sim, 'current', [1], current)
    sim.set_current_limit.assert_not_called()
    sim.output_controls.assert_not_called()


def test_auto_range_keeps_requested_current_and_unselected_channels():
    sim = Instrument(enabled=False)
    assert configure_simulator(sim, [1], dict(range='AUTO', current=.5, output=True))
    assert sim.data[1]['range'] == 3 and sim.data[1]['current'] == 500
    assert sim.data[2]['range'] == 0 and sim.data[2]['current'] == 1000
    assert writes(sim)[-1] == 'OUTP:ONOFF 1(@1)'


def test_debug_selected_channel_low_limit_control_is_fixed():
    page = SimulatorTab(None)
    page.status_timer.stop()
    try:
        combo, current = page.range_combo, page.current_limit
        combo.setCurrentIndex(combo.findData('LOW'))
        assert current.minimum() == current.maximum() == .001
        assert not current.isEnabled()
        combo.setCurrentIndex(combo.findData('AUTO'))
        assert current.isEnabled() and current.maximum() == 1
    finally:
        page.shutdown()
        page.close()
