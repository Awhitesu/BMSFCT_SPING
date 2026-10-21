"""SCPI-level recipe configuration: one final voltage check, no intermediate ON."""
import copy
import os
import re
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import pytest
from PySide6.QtWidgets import QApplication

from core.engine import TestEngine as Engine
from core.simulator_commands import execute_simulator_command
from core.simulator_control import configure_simulator
from devices.manager import DeviceManager
from devices.ngi_83624 import NGI83624

app = QApplication.instance() or QApplication([])
CONFIG = dict(voltage=2.5, current=1.0, range='HIGH', output=True)
TEXT = '2.5V / 1000mA / ON / Range:HIGH / CH:ALL'


@pytest.fixture(autouse=True)
def driver_clock(monkeypatch):
    clock = [0.0]
    monkeypatch.setattr('devices.ngi_83624.time', SimpleNamespace(
        monotonic=lambda: clock[0], sleep=lambda seconds: clock.__setitem__(0, clock[0] + seconds)))
    return clock


class Instrument(NGI83624):
    """A stateful SCPI instrument; production configuration and checking stay real."""
    def __init__(self, count=2, *, enabled=True, mode=0, current=1000, voltage=2.5):
        super().__init__('offline', max_channels=count)
        self.sequence_number = 1
        self.data = {ch: dict(output=enabled, range=mode, current=current, voltage=voltage)
                     for ch in range(1, count + 1)}
        self.io = []
        self.fail = lambda command: False
        self.after_write = lambda command: None
        self.after_query = lambda command: None
        self.bad_voltage = False
        self._verify_output_voltage = Mock(wraps=self._verify_output_voltage)

    def _safe_send(self, payload, retries=2, logger=None):
        for command in payload.decode().strip().splitlines():
            self.io.append(('write', command))
            if self.fail(command):
                self.last_error = 'Injected setting failure: ' + command
                return False
            match = re.fullmatch(r'SOUR(\d+):(RANG|OUTCURR) (.+)', command)
            if match:
                channel, key, value = int(match[1]), match[2], float(match[3])
                if key == 'RANG':
                    assert not self.data[channel]['output'], 'Range switched while channel was ON'
                    self.data[channel]['range'] = int(value)
                    self.data[channel]['current'] = 0  # Hardware can reset the limit on a range change.
                else:
                    assert self.data[channel]['range'] != 2 or value <= 1.000001
                    self.data[channel]['current'] = value
            else:
                match = re.fullmatch(r'(SOUR:VOLT|OUTP:ONOFF) ([\d.]+)\(@([\d,]+)\)', command)
                assert match, command
                for channel in map(int, match[3].split(',')):
                    self.data[channel]['voltage' if match[1] == 'SOUR:VOLT' else 'output'] = (
                        float(match[2]) if match[1] == 'SOUR:VOLT' else bool(int(match[2])))
            self.after_write(command)
        return True

    def _query(self, command, logger=None):
        self.io.append(('query', command))
        match = re.fullmatch(r'(OUTP|SOUR|MEAS)(\d+):(ONOFF|RANG|OUTCURR|VOLT)\?', command)
        assert match, command
        kind, ch, key = match[1], int(match[2]), match[3]
        self.after_query(command)
        if kind == 'MEAS':
            return str(0 if self.bad_voltage or not self.data[ch]['output'] else self.data[ch]['voltage'])
        value = self.data[ch][{'ONOFF': 'output', 'RANG': 'range', 'OUTCURR': 'current', 'VOLT': 'voltage'}[key]]
        return str(int(value) if key in ('RANG', 'ONOFF') else value)

    def _query_channels(self, template, channels, logger=None, prefix=''):
        channels = list(channels)
        if prefix and not self._safe_send(prefix.encode(), logger=logger):
            raise RuntimeError(self.last_error)
        return [self._query(template.format(ch=ch), logger) for ch in channels]


def manager(*instruments):
    result = DeviceManager.__new__(DeviceManager)
    result.simulators = list(instruments)
    result.enabled_devices = {f'sim{slot}': True for slot in range(1, len(instruments) + 1)}
    for number, sim in enumerate(instruments, 1):
        sim.sequence_number = number
    return result


def writes(sim):
    return [command for action, command in sim.io if action == 'write']


@pytest.mark.parametrize('enabled', [True, False])
def test_unchanged_settings_are_freshly_read_and_only_final_on_checks_voltage(enabled):
    sim = Instrument(enabled=enabled)
    assert execute_simulator_command(manager(sim), TEXT, 1, lambda _: None) == (True, None)
    assert writes(sim) == ['OUTP:ONOFF 1(@1,2)']
    sim._verify_output_voltage.assert_called_once()
    assert sim.last_output_confirmation['duration_s'] >= 1
    assert sim.last_output_confirmation['stable_s'] >= .3
    assert sim.last_configuration['changed_ranges'] == []
    for channel in (1, 2):
        assert ('query', f'SOUR{channel}:RANG?') in sim.io
        assert ('query', f'SOUR{channel}:OUTCURR?') in sim.io
    first_measurement = next(i for i, item in enumerate(sim.io) if item[1].startswith('MEAS'))
    assert sim.io.index(('write', 'OUTP:ONOFF 1(@1,2)')) < first_measurement


def test_current_and_voltage_change_only_check_setpoints_before_final_on():
    sim = Instrument(current=500, voltage=3.0)
    assert configure_simulator(sim, [1, 2], CONFIG)
    assert not any('ONOFF 0' in command for command in writes(sim))
    assert any('OUTCURR 1000' in command for command in writes(sim))
    assert 'SOUR:VOLT 2.5(@1,2)' in writes(sim)
    sim._verify_output_voltage.assert_called_once()
    for channel in (1, 2):
        write = sim.io.index(('write', f'SOUR{channel}:OUTCURR 1000'))
        assert sim.io[write + 1] == ('query', f'SOUR{channel}:OUTCURR?')
    assert all(value['voltage'] == 2.5 and value['current'] == 1000 for value in sim.data.values())


def test_next_configuration_rechecks_hardware_instead_of_trusting_previous_success():
    sim = Instrument()
    assert configure_simulator(sim, [1, 2], CONFIG)
    # Simulate an external change after the first successful command.
    for value in sim.data.values():value.update(current=500, voltage=3.0)
    sim.io.clear()
    assert configure_simulator(sim, [1, 2], CONFIG)
    assert 'SOUR:VOLT 2.5(@1,2)' in writes(sim)
    assert all(value['current'] == 1000 and value['voltage'] == 2.5 for value in sim.data.values())
    assert sim._verify_output_voltage.call_count == 2


def test_accepted_write_with_wrong_limit_readback_still_fails_before_on():
    sim = Instrument(current=500)
    def corrupt_limit(command):
        if ':OUTCURR ' in command:
            channel = int(re.search(r'SOUR(\d+)', command)[1])
            sim.data[channel]['current'] = 500
    sim.after_write = corrupt_limit
    assert execute_simulator_command(manager(sim), TEXT, 1, lambda _: None) == (False, None)
    assert not any('ONOFF 1' in command for command in writes(sim))
    assert not any(value['output'] for value in sim.data.values())
    sim._verify_output_voltage.assert_not_called()


@pytest.mark.parametrize('mode,current,target_mode,target_current', [(0, 1000, 'LOW', .0005), (2, .5, 'HIGH', 1.0)])
def test_range_switch_occurs_off_and_restores_requested_limit_without_intermediate_on(mode, current, target_mode, target_current):
    sim = Instrument(mode=mode, current=current, voltage=3.0)
    config = dict(CONFIG, range=target_mode, current=target_current)
    assert configure_simulator(sim, [1, 2], config)
    commands = writes(sim)
    assert commands[0] == 'OUTP:ONOFF 0(@1,2)'
    assert commands[-1] == 'OUTP:ONOFF 1(@1,2)'
    assert sum('ONOFF 1' in command for command in commands) == 1
    assert all(value['current'] == target_current * 1000 for value in sim.data.values())
    sim._verify_output_voltage.assert_called_once()


def test_mixed_states_and_ranges_only_switch_changed_on_channels_and_preserve_unselected():
    sim = Instrument(3)
    sim.data[2].update(range=2, current=.5, output=False)
    before = copy.deepcopy(sim.data[3])
    assert configure_simulator(sim, [1, 2], dict(CONFIG, range='LOW', current=.0005))
    assert writes(sim)[0] == 'OUTP:ONOFF 0(@1)'
    assert writes(sim)[-1] == 'OUTP:ONOFF 1(@1,2)'
    assert sim.data[3] == before


def test_explicit_off_configures_without_measuring_voltage_or_reenabling():
    sim = Instrument(current=500, voltage=3.0)
    assert configure_simulator(sim, [1, 2], dict(CONFIG, output=False))
    assert writes(sim)[0] == 'OUTP:ONOFF 0(@1,2)'
    assert not any('ONOFF 1' in command for command in writes(sim))
    assert not any(command.startswith('MEAS') for _, command in sim.io)
    sim._verify_output_voltage.assert_not_called()
    assert not any(value['output'] for value in sim.data.values())


@pytest.mark.parametrize('failed_setting', ['RANG', 'OUTCURR', 'SOUR:VOLT', 'ONOFF 1'])
def test_failed_settings_or_on_leave_all_targets_off_and_return_failure(failed_setting):
    sim = Instrument(mode=2, current=.5, voltage=3.0)
    sim.fail = lambda command: failed_setting in command
    assert execute_simulator_command(manager(sim), TEXT, 1, lambda _: None) == (False, None)
    assert not any(value['output'] for value in sim.data.values())
    assert not sim.last_configuration['ok']
    if failed_setting != 'ONOFF 1':
        assert not any('ONOFF 1' in command for command in writes(sim))


def test_bad_measured_voltage_is_not_hidden_by_matching_setpoints():
    sim = Instrument()
    sim.bad_voltage = True
    assert execute_simulator_command(manager(sim), TEXT, 1, lambda _: None) == (False, None)
    sim._verify_output_voltage.assert_called_once()
    assert not sim.last_output_confirmation['ok']
    assert not any(value['output'] for value in sim.data.values())


@pytest.mark.parametrize('cancel_at', ['before', 'range', 'measurement'])
def test_cancellation_never_returns_success_or_leaves_partial_configuration_on(cancel_at):
    sim = Instrument(mode=2, current=.5)
    cancelled = threading.Event()
    if cancel_at == 'before':cancelled.set()
    elif cancel_at == 'range':
        sim.after_write = lambda command: cancelled.set() if ':RANG ' in command else None
    else:
        sim.after_query = lambda command: cancelled.set() if command.startswith('MEAS') else None
    assert execute_simulator_command(manager(sim), TEXT, 1, lambda _: None, cancelled.is_set) == (False, None)
    if cancel_at == 'before':
        assert not sim.io
    else:
        assert not any(value['output'] for value in sim.data.values())


def test_cancelled_off_still_reaches_selected_channels():
    sim = Instrument()
    assert execute_simulator_command(manager(sim), TEXT.replace('ON', 'OFF'), 1,
                                     lambda _: None, lambda: True) == (False, None)
    assert not any(value['output'] for value in sim.data.values())
    assert not any(command.startswith('SOUR') for command in writes(sim))


@pytest.mark.parametrize('configuration', [dict(CONFIG, current=True), dict(CONFIG, voltage=float('nan')),
    dict(CONFIG, range='bad'), dict(CONFIG, range='LOW'), dict(CONFIG, output=1)])
def test_invalid_configuration_is_rejected_before_any_io(configuration):
    sim = Instrument()
    with pytest.raises(ValueError):configure_simulator(sim, [1, 2], configuration)
    assert not sim.io


def test_derived_low_limit_constraint_is_checked_before_writes():
    sim = Instrument(mode=2, current=.5)
    with pytest.raises(RuntimeError, match='LOW'):
        configure_simulator(sim, [1, 2], dict(current=1.0, output=True))
    assert not writes(sim)


@pytest.mark.parametrize('channels', [[1, 99], [1, 1], [0], []])
def test_all_requested_routes_are_validated_before_any_io(channels):
    sim = Instrument()
    with pytest.raises(ValueError):manager(sim).configure_simulators(CONFIG, channels)
    assert not sim.io


def test_global_selection_uses_actual_device_counts_and_keeps_other_channels_unchanged():
    one, two = Instrument(2), Instrument(3)
    before = [copy.deepcopy(one.data[1]), copy.deepcopy(two.data[2]), copy.deepcopy(two.data[3])]
    assert execute_simulator_command(manager(one, two), TEXT.replace('CH:ALL', 'CH:2,3'), 1, lambda _: None)[0]
    assert writes(one) == ['OUTP:ONOFF 1(@2)']
    assert writes(two) == ['OUTP:ONOFF 1(@1)']
    assert before == [one.data[1], two.data[2], two.data[3]]


def test_devices_configure_concurrently_and_peer_failure_closes_all_selected_devices():
    barrier = threading.Barrier(3)
    class Driver:
        max_channels = 2
        def __init__(self, failed=False):
            self.failed = failed
            self.outputs = Mock(return_value=True)
        def configure_confirmed(self, channels, configuration, **kwargs):
            barrier.wait(timeout=1)
            if self.failed:raise RuntimeError('peer failed')
            return True
        def output_controls(self, state, channels, logger=None):
            return self.outputs(state, channels)
    drivers = [Driver(), Driver(True), Driver()]
    assert not manager(*drivers).configure_simulators(CONFIG)
    for driver in drivers:
        driver.outputs.assert_called_once_with(False, [1, 2])


def test_engine_registers_combined_success_once_without_parent_retest():
    sim = Instrument(24)
    engine = Engine(device_manager=manager(sim))
    parents, finished = [], []
    engine.step_finished.connect(lambda *result: parents.append(result))
    engine.channel_test_finished.connect(lambda cid, ok: finished.append(ok))
    item = dict(name='simulator', min='PASS', max='--', standard_type='字符串', retry_count='复测1次',
                sub_steps=[dict(type='设置仪表', device='电池模拟器', params=TEXT, is_judgment=True)])
    try:
        assert engine.start_channel_test(1, [item], debug_run=True)
        deadline = time.monotonic() + 3
        while (engine.workers or not finished) and time.monotonic() < deadline:
            app.processEvents(); time.sleep(.001)
        assert finished == [True] and len(parents) == 1 and parents[0][-2:] == (True, 'PASS')
        sim._verify_output_voltage.assert_called_once()
    finally:
        engine.stop_all(); app.processEvents()
