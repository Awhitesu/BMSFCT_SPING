from unittest.mock import Mock, patch
import pytest

from devices.ngi_n3618 import NGIN3618
from devices.manager import DeviceManager


def socket_with(*replies):
    sock = Mock()
    sock.recv.side_effect = replies
    return sock


@pytest.mark.parametrize('identity', [b'ERROR\n', b'NGI,N83624A,SN,1.0\n', b'\n', b'Unknown device\n'])
def test_unidentified_device_cannot_pass_startup(identity):
    sock = socket_with(identity)
    driver = NGIN3618('127.0.0.1')
    with patch('devices.ngi_n3618.socket.socket', return_value=sock):
        assert not driver.connect()
    assert not driver.is_connected
    assert driver.sock is None
    sock.close.assert_called_once()


def test_startup_rechecks_stale_connection_instead_of_reusing_flag():
    driver = NGIN3618('127.0.0.1')
    old = socket_with()
    driver.sock = old
    driver.is_connected = True
    new = socket_with()
    new.connect.side_effect = OSError('device unavailable')
    with patch('devices.ngi_n3618.socket.socket', return_value=new):
        assert not driver.connect()
    assert not driver.is_connected and driver.sock is None
    old.close.assert_called_once()
    new.connect.assert_called_once_with(('127.0.0.1',5025))


@pytest.mark.parametrize('reply', [b'ERROR\n', b'\n', b'2\n', b''])
def test_identity_without_valid_instrument_state_is_not_connected(reply):
    sock = socket_with(b'NGI,N3618,SN,1.0\n', reply)
    driver = NGIN3618('127.0.0.1')
    with patch('devices.ngi_n3618.socket.socket', return_value=sock):
        assert not driver.connect()
    assert not driver.is_connected and driver.sock is None


def test_passive_startup_checks_identity_and_state_with_fragmented_replies():
    sock = socket_with(b'NGI,N36', b'18,SN,1.0\r\n', b'OFF\n')
    driver = NGIN3618('127.0.0.1')
    with patch('devices.ngi_n3618.socket.socket', return_value=sock):
        assert driver.connect()
    assert driver.identity == 'NGI,N3618,SN,1.0'
    assert driver.is_connected
    assert [call.args[0] for call in sock.sendall.call_args_list] == [b'*IDN?\n', b'OUTP?\n']
    driver.disconnect()


@pytest.mark.parametrize('raises', [False,True])
def test_failed_hv_initialization_is_shown_offline_even_with_stale_flag(raises):
    manager = DeviceManager.__new__(DeviceManager)
    keys = ('pcan','opteeq','e2_rfp','easy320','ca550','dut_power','it6333b','tbs2102b','dg2052','dmm34461')
    for key in keys:
        setattr(manager,key,None)
    manager.simulators = []
    manager.rn_boards = {}
    manager.db_manager = None
    manager.is_enabled = lambda key: key == 'hv'
    manager.hv_source = Mock(is_connected=True,ip='127.0.0.1',last_error='no response')
    manager.hv_source.connect.return_value = False
    if raises:
        manager.hv_source.connect.side_effect = OSError('connect failed')
        manager.hv_source.disconnect.side_effect = OSError('close failed')
    assert not manager.init_all_devices()
    assert manager.hv_source.is_connected is False
    assert manager.get_all_device_status()[0]['status'] == '离线'
