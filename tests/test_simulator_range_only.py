"""独立量程仅在LOW需要时先设1mA，不联动输出或电压。"""
import re
import threading
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from core.simulator_control import control_simulator
from devices.ngi_83624 import NGI83624


@pytest.fixture
def simulator():
    sim = NGI83624('offline', max_channels=3)
    sim.modes = {1: 0, 2: 2, 3: 3}
    sim.limits = {1: 1, 2: 1, 3: 1}  # 设备原生单位mA
    sim.sent = []
    sim.after_send = lambda: None
    def send(payload, **_):
        command = payload.decode().strip()
        sim.sent.append(command)
        match = re.fullmatch(r'SOUR(\d+):(RANG|OUTCURR) (\d+)', command)
        assert match, '独立量程操作写入了其他参数：' + command
        channel = int(match[1])
        if match[2] == 'RANG':
            sim.modes[channel] = int(match[3])
        else:
            assert match[3] == '1', 'LOW只允许自动设1mA限流'
            sim.limits[channel] = 1
        sim.after_send()
        return True
    def query(command, *_):
        match = re.fullmatch(r'SOUR(\d+):(OUTCURR|RANG)\?', command)
        assert match, '独立量程操作查询了其他参数：' + command
        channel = int(match[1])
        return str(sim.limits[channel] if match[2] == 'OUTCURR' else sim.modes[channel])
    sim._safe_send = Mock(side_effect=send)
    sim._query = Mock(side_effect=query)
    sim.output_controls = Mock(side_effect=AssertionError('量程操作不应开关输出'))
    sim._verify_output_voltage = Mock(side_effect=AssertionError('量程操作不应联动输出确认'))
    return sim


@pytest.mark.parametrize('mode,code', [('LOW', 2), ('HIGH', 0), ('AUTO', 3)])
def test_only_range_writes_for_selected_channels(simulator, mode, code):
    sim = simulator
    initial = dict(sim.modes)
    expected = [f'SOUR{ch}:RANG {code}' for ch in (1, 3) if initial[ch] != code]
    assert control_simulator(sim, 'range', [1, 3], mode, batch=True)
    assert sim.sent == expected
    assert sim.modes[2] == initial[2]
    assert sim.limits == {1: 1, 2: 1, 3: 1}
    sim.output_controls.assert_not_called()
    sim._verify_output_voltage.assert_not_called()


def test_same_range_is_read_only(simulator):
    simulator.last_error = 'previous error'
    assert control_simulator(simulator, 'range', [2], 'LOW')
    assert simulator.sent == []
    assert simulator.last_error == ''


@pytest.mark.parametrize('initial_mode', [0, 2, 3])
def test_low_sets_and_confirms_one_ma_before_range_for_selected_channels(simulator, initial_mode):
    simulator.modes[1] = initial_mode
    simulator.limits.update({1: 100, 3: 1000})
    assert control_simulator(simulator, 'range', [1, 3], 'LOW')
    expected = ['SOUR1:OUTCURR 1']
    if initial_mode != 2:
        expected.append('SOUR1:RANG 2')
    assert simulator.sent == expected + ['SOUR3:OUTCURR 1', 'SOUR3:RANG 2']
    assert simulator.limits == {1: 1, 2: 1, 3: 1}
    assert simulator.modes == {1: 2, 2: 2, 3: 2}
    simulator.output_controls.assert_not_called()
    simulator._verify_output_voltage.assert_not_called()


@pytest.mark.parametrize('mode', ['HIGH', 'AUTO'])
def test_non_low_range_preserves_existing_current_limit(simulator, mode):
    simulator.limits[1] = 100
    assert control_simulator(simulator, 'range', [1], mode)
    assert simulator.limits[1] == 100
    assert all(':OUTCURR ' not in command for command in simulator.sent)


def test_failed_low_limit_confirmation_does_not_switch_range(simulator):
    simulator.limits[1] = 100
    original_query = simulator._query.side_effect
    simulator._query.side_effect = lambda command, *_: '100' if command == 'SOUR1:OUTCURR?' else original_query(command)
    with pytest.raises(RuntimeError, match='OUTCURR'):
        control_simulator(simulator, 'range', [1], 'LOW')
    assert simulator.sent == ['SOUR1:OUTCURR 1'] * 3
    assert simulator.modes[1] == 0
    simulator.output_controls.assert_not_called()


def test_cancel_after_low_limit_does_not_switch_range_or_restore_limit(simulator):
    simulator.limits[1] = 100
    cancelled = threading.Event()
    simulator.after_send = cancelled.set
    with pytest.raises(RuntimeError, match='cancelled'):
        control_simulator(simulator, 'range', [1], 'LOW', cancelled=cancelled.is_set)
    assert simulator.sent == ['SOUR1:OUTCURR 1']
    assert simulator.limits[1] == 1
    assert simulator.modes[1] == 0
    simulator.output_controls.assert_not_called()


@pytest.mark.parametrize('legacy', [False, True])
def test_sequence_range_step_uses_automatic_low_limit(simulator, legacy):
    simulator.limits[3] = 100
    manager = SimpleNamespace(_get_sim_and_ch=lambda _: (simulator, 3))
    if legacy:
        from core.simulator_commands import execute_simulator_command
        ok, _ = execute_simulator_command(manager, 'Range:LOW / CH:27', 1, lambda _: None)
    else:
        from core.device_steps import encode, execute
        ok, _ = execute(manager, encode('simulator', 'range', {'channel': 27, 'range': 'LOW'}), {}, lambda _: None)
    assert ok
    assert simulator.sent == ['SOUR3:OUTCURR 1', 'SOUR3:RANG 2']
    simulator.output_controls.assert_not_called()


def test_cancel_after_first_range_write_does_not_touch_output_or_rollback(simulator):
    cancelled = threading.Event()
    simulator.after_send = cancelled.set
    with pytest.raises(RuntimeError, match='cancelled'):
        control_simulator(simulator, 'range', [1, 3], 'LOW', cancelled=cancelled.is_set)
    assert simulator.sent == ['SOUR1:RANG 2']
    assert simulator.modes == {1: 2, 2: 2, 3: 3}
    simulator.output_controls.assert_not_called()


def test_failed_range_write_does_not_trigger_output_cleanup(simulator):
    simulator._safe_send.side_effect = None
    simulator._safe_send.return_value = False
    simulator.last_error = 'range rejected'
    with pytest.raises(RuntimeError, match='range rejected'):
        control_simulator(simulator, 'range', [1], 'LOW')
    simulator.output_controls.assert_not_called()
    assert all(call.args[0] == b'SOUR1:RANG 2\n' for call in simulator._safe_send.call_args_list)
