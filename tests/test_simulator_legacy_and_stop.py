import threading
from types import SimpleNamespace
from unittest.mock import Mock, ANY

import pytest

from core.simulator_commands import execute_simulator_command
from core.engine import ChannelWorker, TestEngine as Engine
from test_shutdown_and_zlg_failures import manager_stub


def simulator_manager():
    sim = SimpleNamespace(set_range=Mock(return_value=True))
    manager = SimpleNamespace(
        _get_sim_and_ch=lambda ch: (sim, ch),
        set_voltage=Mock(return_value=True), set_current=Mock(return_value=True),
        output_control=Mock(return_value=True), broadcast_voltage=Mock(return_value=True),
        broadcast_current=Mock(return_value=True), broadcast_range=Mock(return_value=True),
        broadcast_output=Mock(return_value=True))
    return manager, sim


@pytest.mark.parametrize('mode', ['HIGH', 'LOW', 'AUTO'])
def test_legacy_range_preserves_requested_mode(mode):
    manager, sim = simulator_manager()
    assert execute_simulator_command(manager, f'Range:{mode} / CH:2', 1, lambda _: None)[0]
    sim.set_range.assert_called_once_with(2, mode, logger=ANY)
    manager.set_voltage.assert_not_called()
    manager.output_control.assert_not_called()


@pytest.mark.parametrize('stage', ['broadcast_voltage', 'broadcast_current', 'broadcast_range'])
@pytest.mark.parametrize('state', ['ON', 'OFF'])
def test_failed_bulk_setup_does_not_enable_but_still_honors_off(stage, state):
    manager, _ = simulator_manager()
    getattr(manager, stage).return_value = False
    assert not execute_simulator_command(manager,
        f'3.7V / 100mA / {state} / Range:HIGH / CH:ALL', 1, lambda _: None)[0]
    if state == 'ON':
        manager.broadcast_output.assert_not_called()
    else:
        assert manager.broadcast_output.call_args.args == (False,)


def test_invalid_numeric_is_not_silently_zero_and_off_is_still_attempted():
    manager, _ = simulator_manager()
    with pytest.raises(ValueError):
        execute_simulator_command(manager, 'badV / OFF / CH:2', 1, lambda _: None)
    manager.set_voltage.assert_not_called()
    assert manager.output_control.call_args.args == (2, False)


def test_legacy_configuration_cancel_does_not_continue_to_on():
    manager, _ = simulator_manager()
    cancelled = threading.Event()
    manager.broadcast_current.side_effect = lambda *args, **kw: (cancelled.set(), True)[1]
    assert not execute_simulator_command(manager, '3.7V / 100mA / ON / CH:ALL',
                                        1, lambda _: None, cancelled.is_set)[0]
    manager.broadcast_voltage.assert_not_called()
    manager.broadcast_output.assert_not_called()


def test_disconnected_previously_connected_outputs_are_not_reported_safe():
    manager = manager_stub()
    manager.hv_source = SimpleNamespace(is_connected=False, _ever_connected=True)
    assert not manager.safe_shutdown_outputs()
    assert manager.last_shutdown_unverified
    # Reconfiguration can still recover a changed IP, but must retain the warning.
    assert manager.safe_shutdown_outputs(check_offline=False)
    assert manager.last_shutdown_unverified


def test_never_connected_disabled_output_does_not_block_shutdown():
    manager = manager_stub()
    manager.hv_source = SimpleNamespace(is_connected=False)
    assert manager.safe_shutdown_outputs()


@pytest.mark.parametrize('recipe', [[], [{'name':'#disabled','sub_steps':[]}],
                                  [{'name':'disabled','skip_runtime':True,'sub_steps':[]}]])
def test_zero_executed_items_cannot_produce_a_pass(recipe):
    database = Mock()
    engine = Engine(db_manager=database)
    assert not engine.start_channel_test(1, recipe)
    assert not engine.workers and not engine.threads
    database.start_new_test.assert_not_called()


def test_worker_log_storage_is_bounded():
    worker = ChannelWorker(1, [])
    for index in range(2000):
        worker.log_message.emit(1, str(index))
    retained = sum(len(value) for key, value in vars(worker).items()
                   if 'log_history' in key and isinstance(value, list))
    assert retained <= worker.MAX_LOG_HISTORY
