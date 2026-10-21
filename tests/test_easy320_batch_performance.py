from types import SimpleNamespace
from unittest.mock import Mock,patch
from devices.easy320_driver import Easy320Controller


def reply(bits=None,error=None):
    return SimpleNamespace(isError=lambda:error is not None,bits=bits or [],exception_code=error)


def test_24_off_points_use_one_write_and_one_read():
    with patch('devices.easy320_driver.ModbusTcpClient'),patch('devices.easy320_driver.time.sleep') as sleep:
        driver=Easy320Controller('unused');driver.is_connected=True
        driver.client.write_coils.return_value=reply()
        driver.client.read_coils.return_value=reply([False]*24)
        assert driver.control_selected_relays(range(24),False)
        driver.client.write_coils.assert_called_once_with(address=0xFC08,values=[False]*24,device_id=1)
        driver.client.read_coils.assert_called_once_with(address=0xFC08,count=24,device_id=1)
        sleep.assert_called_once_with(.05)


def test_mismatch_and_timeout_do_not_trigger_blind_fallback():
    with patch('devices.easy320_driver.ModbusTcpClient'),patch('devices.easy320_driver.time.sleep'):
        driver=Easy320Controller('unused');driver.is_connected=True
        driver.write_relay=Mock()
        driver.client.write_coils.return_value=reply()
        driver.client.read_coils.return_value=reply([True,False])
        assert not driver.control_selected_relays([0,1],False)
        driver.client.write_coils.side_effect=TimeoutError('timeout')
        assert not driver.control_selected_relays([0,1],False)
        driver.write_relay.assert_not_called()


def test_explicit_unsupported_function_uses_verified_single_point_path():
    with patch('devices.easy320_driver.ModbusTcpClient'):
        driver=Easy320Controller('unused');driver.is_connected=True
        driver.client.write_coils.return_value=reply(error=1)
        driver.write_relay=Mock(return_value=True)
        driver.read_selected_relays=Mock(return_value=[False,False])
        assert driver.control_selected_relays([1,8],False)
        assert [c.args for c in driver.write_relay.call_args_list]==[(1,False),(8,False)]
