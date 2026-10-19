import threading
from unittest.mock import Mock

import pytest

from core.board_control import control_board


def board():
    d = Mock()
    d._lock = threading.RLock()
    d.setting = {'channels': 2}
    return d


@pytest.mark.parametrize('reply', [True, False, [], [False], [False, True], [0, 0]])
def test_shutdown_rejects_incomplete_or_inconsistent_relay_readback(reply):
    d = board()
    d.all_relays.return_value = reply
    with pytest.raises(RuntimeError):
        control_board(d, 'all_relays', state=False)


def test_cancel_blocks_activation_after_lock_wait_but_not_release():
    d = board()
    stop = threading.Event()
    started = threading.Event()
    failures = []
    def activate():
        started.set()
        try:
            control_board(d, 'relay', channel=1, state=True, cancelled=stop.is_set)
        except RuntimeError as exc:
            failures.append(str(exc))
    with d._lock:
        worker = threading.Thread(target=activate)
        worker.start()
        assert started.wait(2)
        stop.set()
    worker.join(2)
    assert not worker.is_alive() and failures
    d.set_relay.assert_not_called()
    d.all_relays.return_value = [False, False]
    assert control_board(d, 'all_relays', state=False, cancelled=stop.is_set) == [False, False]


def test_insulation_off_ignores_stale_invalid_setpoint():
    d = board()
    d.set_resistance_output.return_value = {'output': False, 'resistance': 0}
    assert control_board(d, 'insulation', state=False, resistance=float('nan'))['output'] is False
    d.set_resistance_output.assert_called_once_with(False, 0)


def test_zero_resistance_is_valid_confirmed_result():
    d = board()
    d.set_resistance.return_value = 0
    assert control_board(d, 'resistance', channel=2, resistance=0, cancelled=lambda: True) == 0


@pytest.mark.parametrize('reply', [True, False, None, 999])
def test_resistance_requires_exact_numeric_confirmation(reply):
    d = board()
    d.set_resistance.return_value = reply
    with pytest.raises(RuntimeError):
        control_board(d, 'resistance', resistance=1000)
