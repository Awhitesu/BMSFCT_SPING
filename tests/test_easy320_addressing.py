"""Verify actual Modbus addresses after moving DO0 to 0xFC00."""
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.easy320_channels import normalize_channels
from core.relay_combination import validate
from devices.easy320_driver import Easy320Controller


def reply(**kwargs):
    return SimpleNamespace(isError=lambda:False, **kwargs)


@pytest.mark.parametrize('index,address', [(0, 0xFC00), (8, 0xFC08), (16, 0xFC10), (1023, 0xFFFF)])
@pytest.mark.parametrize('register_fallback', [False, True])
def test_single_write_and_verification_use_the_new_base(index, address, register_fallback):
    with patch('devices.easy320_driver.ModbusTcpClient'), patch('devices.easy320_driver.time.sleep'):
        driver = Easy320Controller('unused')
        driver.is_connected = True
        driver.client.write_coil.return_value = SimpleNamespace(isError=lambda:register_fallback)
        driver.client.write_register.return_value = reply()
        driver.client.read_coils.return_value = reply(bits=[True])
        driver.client.read_holding_registers.return_value = reply(registers=[1])
        assert driver.write_relay(index, True)
        driver.client.write_coil.assert_called_once_with(address=address, value=True, device_id=1)
        if register_fallback:
            driver.client.write_register.assert_called_once_with(address=address, value=1, device_id=1)
            driver.client.read_holding_registers.assert_called_once_with(address=address, count=1, device_id=1)
        else:
            driver.client.read_coils.assert_called_once_with(address=address, count=1, device_id=1)


def test_connection_handshake_reads_do_zero_at_fc00():
    with patch('devices.easy320_driver.ModbusTcpClient'), patch('socket.create_connection'):
        driver = Easy320Controller('unused')
        driver.client.connect.return_value = True
        driver.client.read_coils.return_value = reply(bits=[False])
        assert driver.connect()
        driver.client.read_coils.assert_called_once_with(address=0xFC00, count=1, device_id=1)


def test_sparse_read_uses_new_base_with_actual_do_offsets():
    with patch('devices.easy320_driver.ModbusTcpClient'):
        driver = Easy320Controller('unused')
        driver.is_connected = True
        driver.client.read_coils.return_value = reply(bits=[False])
        assert driver.read_selected_relays([16, 0, 8]) == [False, False, False]
        assert [c.kwargs['address'] for c in driver.client.read_coils.call_args_list] == [0xFC00, 0xFC08, 0xFC10]


def test_plc_holding_register_addresses_remain_exact():
    with patch('devices.easy320_driver.ModbusTcpClient'), patch('devices.easy320_driver.time.sleep'):
        driver = Easy320Controller('unused')
        driver.is_connected = True
        driver.client.read_holding_registers.return_value = reply(registers=[3])
        driver.client.write_register.return_value = reply()
        assert driver.read_register(address=1, count=1) == [3]
        assert driver.write_register(1, 3)
        driver.client.write_register.assert_called_once_with(address=1, value=3, device_id=1)
        assert all(c.kwargs['address'] == 1 for c in driver.client.read_holding_registers.call_args_list)


def test_import_combination_and_driver_share_the_full_16_bit_address_boundary():
    assert normalize_channels([(2, 'DO1777', 'last')])[0]['index'] == 1023
    assert validate([dict(index=1023, on=True)]) == [dict(index=1023, on=True)]
    with pytest.raises(ValueError):normalize_channels([(2, 'DO2000', 'out of range')])
    with pytest.raises(ValueError):validate([dict(index=1024, on=True)])
    with patch('devices.easy320_driver.ModbusTcpClient'):
        driver = Easy320Controller('unused')
        driver.connect = Mock()
        with pytest.raises(ValueError):driver.write_relay(1024, True)
        driver.connect.assert_not_called()
        driver.client.write_coil.assert_not_called()


def test_legacy_step_validation_allows_the_last_mapped_address():
    from core.engine import ChannelWorker, SubStep, SubStepType
    manager = SimpleNamespace(easy320=Mock(write_relay=Mock(return_value=True)),
                              db_manager=SimpleNamespace(load_sys_config=lambda:{
                                  'easy320_channels':[dict(do='DO1777', name='last')]}))
    worker = ChannelWorker(1, [], device_manager=manager)
    ok, value = worker._execute_sub_step_logic(SubStep(SubStepType.SET_INSTRUMENT,
        dict(device='Easy320', action='闭合勾选通道', params='1024')))
    assert ok, value
    manager.easy320.write_relay.assert_called_once_with(1023, True)
