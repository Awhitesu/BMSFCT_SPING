from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock, patch
import threading

import pytest
from PySide6.QtWidgets import QApplication

from ui.dialogs.zlg_recipe_dialog import ZlgRecipeDialog, validate_zlg_config
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab
from core.can_choices import CAN_CHANNELS

app = QApplication.instance() or QApplication([])


def config():
    return dict(ip='192.0.2.10', port=8000, send_channel=3,
                channels=[dict(enabled=True, nominal=125000+i*0, data=2000000, termination=i==2) for i in range(4)])


def test_dialog_roundtrip_without_global_send_selector():
    value = config()
    dialog = ZlgRecipeDialog(value)
    try:
        assert dialog.get_config() == value
        assert not hasattr(dialog,'send_channel')
        dialog.controls[2][0].setChecked(False)
        assert dialog.get_config()['send_channel'] == 1
        for controls in dialog.controls:controls[0].setChecked(False)
        with pytest.raises(ValueError, match='至少启用'):dialog.get_config()
        assert value['channels'][2]['enabled'] is True
    finally: dialog.close()


def test_recipe_has_two_devices_and_no_can_and_load_does_not_popup():
    db = Mock(); db.list_recipes.return_value=[]
    page = ConfigTab(db)
    try:
        assert [page.recipe_pcan_channel.itemText(i) for i in range(page.recipe_pcan_channel.count())] == ['PCAN_USBBUS1', 'ZLG CANFDNET-400U', '不使用 CAN']
        value = config()
        recipe = dict(items=[], can_config=dict(channel='ZLG_CAN3', zlg=value))
        with patch.object(ZlgRecipeDialog, 'exec') as popup:
            page.load_recipe_to_tree(recipe)
            popup.assert_not_called()
        saved = page._collect_recipe_data('test')['can_config']
        assert saved['zlg'] == value and saved['channel'] == 'ZLG_CAN3'
        with patch.object(ZlgRecipeDialog, 'exec', return_value=1) as popup, \
             patch.object(ZlgRecipeDialog, 'get_config', return_value=value):
            page.recipe_pcan_channel.setCurrentIndex(0)
            page.recipe_pcan_channel.setCurrentIndex(1)
            popup.assert_called_once()
        assert CAN_CHANNELS == ('PCAN_USBBUS1','ZLG_CAN1','ZLG_CAN2','ZLG_CAN3','ZLG_CAN4')
    finally: page.close()


@pytest.mark.parametrize('fail', [False, True])
def test_start_applies_all_channels_and_stops_on_failure(fail):
    driver = Mock()
    driver.lock = threading.RLock()
    driver.connect.return_value = True
    driver.disconnect.return_value = True
    if fail: driver.bus.configure_channels.side_effect = RuntimeError('apply failed')
    page = SimpleNamespace(_prepare_cancel=threading.Event(), engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=driver)))
    value = config()
    ok, message = OverviewTab._apply_recipe_hardware(page, {'can_config':dict(channel='ZLG_CAN3', mode='CAN-FD', zlg=value)})
    assert ok is not fail
    assert driver.zlg_ip == value['ip'] and driver.zlg_port == value['port']
    driver.bus.configure_channels.assert_called_once_with(value['channels'], False)
    if fail:
        assert 'apply failed' in message
        assert driver.disconnect.call_count == 2


def test_invalid_config_rejected_before_disconnect():
    value = deepcopy(config())
    for row in value['channels']:row['enabled'] = False
    with pytest.raises(ValueError): validate_zlg_config(value)
