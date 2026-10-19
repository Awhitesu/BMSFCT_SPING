from unittest.mock import Mock, patch
from PySide6.QtWidgets import QApplication
from ui.tabs.config_tab import ConfigTab

app = QApplication.instance() or QApplication([])


def test_barcode_rule_dialog_preserves_sequence_on_accept_and_cancel():
    db = Mock()
    db.list_recipes.return_value = []
    db.load_sys_config.return_value = {}
    page = ConfigTab(db)
    try:
        recipe = {'items': [{'name': 'keep', 'min': '0', 'max': '1', 'sub_steps': []}]}
        page.load_recipe_to_tree(recipe)
        with patch('ui.dialogs.ai_barcode_dialog.AiBarcodeRuleDialog') as dialog:
            dialog.return_value.exec.return_value = True
            dialog.return_value.get_generated_regex.return_value = '^SN[0-9]+$'
            page.open_ai_barcode_dialog()
            assert page.edit_sn_regex.text() == '^SN[0-9]+$'
            dialog.return_value.exec.return_value = False
            page.open_ai_barcode_dialog()
        assert page.step_tree.topLevelItemCount() == 1
        assert page.step_tree.topLevelItem(0).text(0) == 'keep'
    finally:
        page.close()
