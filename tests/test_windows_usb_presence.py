import ctypes
from unittest.mock import Mock, patch

import pytest

from devices.windows_usb import present_usb_devices, UsbDevice


def fake_api():
    setup, config = Mock(), Mock()
    setup.SetupDiGetClassDevsW.return_value = 123

    def enumerate_node(handle, index, info):
        if index == 0:
            info._obj.DevInst = 42
            return True
        ctypes.set_last_error(259)
        return False

    def get_id(handle, info, buffer, capacity, needed):
        buffer.value = r'USB\VID_045B&PID_82A1\TEST_E2'
        return True

    def get_status(status, problem, instance, flags):
        status._obj.value = 0x08
        problem._obj.value = 0
        return 0

    setup.SetupDiEnumDeviceInfo.side_effect = enumerate_node
    setup.SetupDiGetDeviceInstanceIdW.side_effect = get_id
    config.CM_Get_DevNode_Status.side_effect = get_status
    return setup, config


def test_enumeration_requests_present_usb_nodes_and_releases_handle():
    setup, config = fake_api()
    with patch('devices.windows_usb.ctypes.WinDLL', side_effect=[setup, config]):
        assert present_usb_devices() == [UsbDevice(r'USB\VID_045B&PID_82A1\TEST_E2', True, 0)]
    setup.SetupDiGetClassDevsW.assert_called_once_with(None, 'USB', None, 0x06)
    setup.SetupDiDestroyDeviceInfoList.assert_called_once_with(123)


def test_enumeration_failure_releases_handle_instead_of_returning_empty_success():
    setup, config = fake_api()

    def denied(*args):
        ctypes.set_last_error(5)
        return False

    setup.SetupDiGetDeviceInstanceIdW.side_effect = denied
    with patch('devices.windows_usb.ctypes.WinDLL', side_effect=[setup, config]), pytest.raises(OSError):
        present_usb_devices()
    setup.SetupDiDestroyDeviceInfoList.assert_called_once_with(123)


def test_driver_problem_is_kept_for_caller_to_reject():
    setup, config = fake_api()

    def problem(status, code, instance, flags):
        status._obj.value = 0x400
        code._obj.value = 22
        return 0

    config.CM_Get_DevNode_Status.side_effect = problem
    with patch('devices.windows_usb.ctypes.WinDLL', side_effect=[setup, config]):
        node, = present_usb_devices()
    assert not node.started and node.problem_code == 22
