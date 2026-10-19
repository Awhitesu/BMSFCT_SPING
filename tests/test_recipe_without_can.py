import threading
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from core.recipe_can_requirement import validate_recipe_can
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication([])


def test_no_can_roundtrip_and_disabled_parameters():
    db = Mock(); db.list_recipes.return_value = []
    page = ConfigTab(db)
    try:
        page.recipe_pcan_channel.setCurrentText('不使用 CAN')
        assert not page.recipe_can_mode.isEnabled()
        assert not page.recipe_nominal_bitrate.isEnabled()
        assert not page.recipe_data_bitrate.isEnabled()
        saved = page._collect_recipe_data('no-can')
        assert saved['can_config'] == {}
        page.recipe_pcan_channel.setCurrentIndex(0)
        page.load_recipe_to_tree(saved)
        assert page.recipe_pcan_channel.currentData() == 'NONE'
        editor = StepDialog(page)
        assert '不使用 CAN' in editor.can_binding_label.text()
        assert not editor.can_physical_channel.isEnabled()
        editor.close()
        page.load_recipe_to_tree(dict(items=[], can_config={'channel':'PCAN_USBBUS1'}))
        assert page.recipe_pcan_channel.currentData() == 'PCAN_USBBUS1'
    finally:
        page.close()


def test_start_without_can_never_touches_connected_driver():
    driver = Mock()
    page = SimpleNamespace(_prepare_cancel=threading.Event(), engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=driver)))
    assert OverviewTab._apply_recipe_hardware(page, dict(can_config={}, items=[]))[0]
    driver.connect.assert_not_called(); driver.disconnect.assert_not_called(); driver.configure.assert_not_called()


@pytest.mark.parametrize('device,stype', [('CAN 交互','CAN发送'), ('UDS','CAN交互'), ('3.5H EOL','3.5HEOL协议'), ('APP刷写','APP刷写')])
def test_can_steps_blocked_before_hardware_access(device, stype):
    recipe = dict(can_config={}, items=[dict(name='test', sub_steps=[dict(device=device,type=stype)])])
    with pytest.raises(ValueError, match='需要 CAN'):
        validate_recipe_can(recipe)
    driver = Mock()
    page = SimpleNamespace(_prepare_cancel=threading.Event(), engine=SimpleNamespace(device_manager=SimpleNamespace(pcan=driver)))
    assert not OverviewTab._apply_recipe_hardware(page, recipe)[0]
    driver.connect.assert_not_called()
    recipe['items'][0]['skip_runtime'] = True
    validate_recipe_can(recipe)


def test_power_only_does_not_require_can():
    validate_recipe_can(dict(can_config={}, items=[dict(sub_steps=[dict(device='IT6722A',type='设置仪表')])]))
