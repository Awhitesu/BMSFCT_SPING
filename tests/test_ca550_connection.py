from unittest.mock import Mock, patch
import pytest

from devices.ca550_driver import CA550Controller


def serial_reply(identity=b'YOKOGAWA,CA550,SN,1.0\r\n', state=b'SO0\r\n'):
    port = Mock(is_open=True)
    port.readline.side_effect = [identity,state]
    port.read_all.return_value = b''
    port.write.side_effect = lambda data: len(data)
    return port


@pytest.mark.parametrize('idn', [b'\r\n', b'ERROR\r\n', b'error,CA550\r\n', b'YOKOGAWA,CA500,SN,1\r\n', b'12345\r\n'])
def test_wrong_device_or_error_reply_cannot_pass_connection(idn):
    port = serial_reply(idn)
    driver = CA550Controller('COM1')
    with patch('devices.ca550_driver.serial.Serial', return_value=port), patch('devices.ca550_driver.time.sleep'):
        assert not driver.connect()
    assert not driver.is_connected and driver.ser is None
    assert driver.last_error
    port.close.assert_called_once()


@pytest.mark.parametrize('state', [b'ERROR\r\n', b'\r\n', b'SO2\r\n'])
def test_identity_without_valid_output_state_is_offline(state):
    port = serial_reply(state=state)
    driver = CA550Controller('COM1')
    with patch('devices.ca550_driver.serial.Serial', return_value=port), patch('devices.ca550_driver.time.sleep'):
        assert not driver.connect()
    assert not driver.is_connected and driver.ser is None


def test_connection_is_passive_and_model_confirmed():
    port = serial_reply()
    driver = CA550Controller('COM1')
    with patch('devices.ca550_driver.serial.Serial', return_value=port), patch('devices.ca550_driver.time.sleep'):
        assert driver.connect()
    assert driver.identity == 'YOKOGAWA,CA550,SN,1.0'
    assert [c.args[0] for c in port.write.call_args_list] == [b'*IDN?\r\n', b'SO?\r\n']
    driver.disconnect()


def test_handshake_exception_closes_port():
    port = serial_reply()
    port.write.side_effect = OSError('serial broken')
    driver = CA550Controller('COM1')
    with patch('devices.ca550_driver.serial.Serial', return_value=port), patch('devices.ca550_driver.time.sleep'):
        assert not driver.connect()
    port.close.assert_called_once()
    assert driver.ser is None and not driver.is_connected


def test_partial_serial_write_is_not_success():
    port = serial_reply()
    port.write.side_effect = None
    port.write.return_value = 1
    driver = CA550Controller('COM1')
    with patch('devices.ca550_driver.serial.Serial', return_value=port), patch('devices.ca550_driver.time.sleep'):
        assert not driver.connect()
    assert driver.ser is None and not driver.is_connected


def test_port_change_is_not_blocked_by_previous_port_cooldown():
    driver = CA550Controller('COM1')
    with patch('devices.ca550_driver.time.monotonic', return_value=100), patch('devices.ca550_driver.serial.Serial', side_effect=OSError('unavailable')):
        assert not driver.connect()
    driver.port = 'COM2'
    port = serial_reply()
    with patch('devices.ca550_driver.time.monotonic', return_value=101), patch('devices.ca550_driver.serial.Serial', return_value=port), patch('devices.ca550_driver.time.sleep'):
        assert driver.connect()
    driver.disconnect()


def test_blank_port_removes_stale_connected_state():
    driver = CA550Controller('')
    port = serial_reply()
    driver.ser,driver.is_connected = port,True
    assert not driver.connect()
    assert not driver.is_connected and driver.ser is None
    port.close.assert_called_once()


def test_query_timeout_clears_connection_and_closes_port():
    port = serial_reply(b'')
    driver = CA550Controller('COM1')
    driver.ser,driver.is_connected = port,True
    with patch('devices.ca550_driver.time.sleep'):
        assert driver.get_idn().startswith('ERROR:')
    assert not driver.is_connected and driver.ser is None
