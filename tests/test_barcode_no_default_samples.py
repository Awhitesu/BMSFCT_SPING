from PySide6.QtWidgets import QApplication
from ui.dialogs.ai_barcode_dialog import AiBarcodeRuleDialog

app = QApplication.instance() or QApplication([])


def test_no_example_samples_or_accept_all_rule():
    dialog = AiBarcodeRuleDialog()
    try:
        assert dialog.samples == []
        assert dialog.samples_list_widget.count() == 0
        assert dialog.get_generated_regex() == ''
        dialog.verify_input.setText('ANYTHING')
        assert '通过' not in dialog.lbl_verify_status.text()
        dialog.scan_input.setText('AB001 AB002')
        dialog._on_add_sample_from_input()
        assert dialog.samples == ['AB001', 'AB002']
        assert dialog.get_generated_regex()
        dialog._on_clear_all_samples()
        assert dialog.get_generated_regex() == ''
    finally:
        dialog.close()


def test_existing_recipe_rule_is_retained_without_fake_samples():
    dialog = AiBarcodeRuleDialog(initial_regex='^REAL[0-9]+$', default_samples=[])
    try:
        assert dialog.samples == []
        assert dialog.get_generated_regex() == '^REAL[0-9]+$'
    finally:
        dialog.close()
