import pytest
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QApplication, QDialog
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication([])


@pytest.fixture
def page():
    widget = StepDialog()
    widget.category_combo.setCurrentIndex(1)
    widget.device_combo.setCurrentIndex(1)
    for i in range(3):
        widget._append_uds_table_row(f'row{i}', f'22 F1 {0x90+i:02X}', '', 'ASCII(offset=3,len=2)', f'V{i}')
    widget.uds_tabs.setCurrentIndex(2)
    yield widget
    widget.close()


def test_click_edit_and_save_replaces_only_original_preserves_output(page):
    page.uds_table.item(1,6).setCheckState(Qt.Checked)
    page.uds_table.cellClicked.emit(1,1)
    assert page.uds_tabs.currentIndex()==2
    assert page.uds_request.text()=='22 F1 91'
    assert page.uds_result_expected.text()=='V1'
    page.uds_request.setText('22 F1 A0')
    page.uds_extract_mode.setCurrentIndex(2)
    page.uds_extract_len.setValue(2)
    page.uds_extract_scale.setValue(.1)
    page.uds_result_expected.setText('12.3')
    page.uds_save_edit.click()
    assert page.uds_tabs.currentIndex()==2
    assert page.uds_table.rowCount()==3
    assert page.uds_table.item(0,1).text()=='22 F1 90'
    assert page.uds_table.item(1,1).text()=='22 F1 A0'
    assert 'CALC(' in page.uds_table.item(1,4).text()
    assert page.uds_table.item(1,5).text()=='12.3'
    assert page.uds_table.item(1,6).checkState()==Qt.Checked
    assert page.uds_table.item(1,0).text()=='row1'


def test_header_add_button_becomes_save_while_editing(page):
    page.load_uds_row(0)
    page.uds_request.setText('22 F1 AA')
    page.uds_result_expected.setText('AA')
    page.uds_tabs.setCurrentIndex(2)
    page.uds_add_btn.click()
    assert page.uds_table.rowCount()==3
    assert page.uds_table.item(0,1).text()=='22 F1 AA'


def test_reordering_keeps_edit_target_identity(page):
    page.load_uds_row(0)
    page.uds_request.setText('22 F1 AA')
    page.uds_table.selectRow(0)
    page.move_uds_row(1)
    assert page._uds_edit_row()==1
    page.uds_save_edit.click()
    assert page.uds_table.item(1,1).text()=='22 F1 AA'
    assert page.uds_table.item(0,1).text()=='22 F1 91'


def test_deleting_before_and_deleting_target_cannot_edit_wrong_row(page):
    page.load_uds_row(2)
    page.uds_request.setText('22 F1 AA')
    page.uds_table.selectRow(0);page.delete_uds_row()
    assert page._uds_edit_row()==1
    page.uds_save_edit.click()
    assert page.uds_table.item(1,1).text()=='22 F1 AA'
    page.load_uds_row(1)
    page.uds_table.selectRow(1);page.delete_uds_row()
    assert page._uds_edit_item is None
    assert page.uds_request.text()==''
    assert page.uds_table.rowCount()==1


def test_cancel_discards_draft_and_new_request_inserts_below_selection(page):
    page.load_uds_row(0)
    page.uds_request.setText('22 F1 AA')
    page.uds_cancel_edit.click()
    assert page.uds_table.item(0,1).text()=='22 F1 90'
    page.uds_request.setText('22 F1 BB')
    page.uds_add_btn.click()
    assert page.uds_table.rowCount()==4
    assert page.uds_table.item(1,1).text()=='22 F1 BB'
    assert page.uds_table.item(2,1).text()=='22 F1 91'


def test_insert_keeps_existing_output_and_assigns_options_to_new_row(page):
    page.uds_table.item(1,6).setCheckState(Qt.Checked)
    output=page.uds_table.item(1,0)
    page.uds_table.selectRow(0)
    page.uds_request.setText('22 F1 BB')
    page.uds_workbench.address.setCurrentIndex(2)
    page.uds_add_btn.click()
    assert output.row()==2 and page.uds_table.item(2,6).checkState()==Qt.Checked
    assert page.uds_table.item(1,0).data(Qt.UserRole)['address']=='functional'
    assert page.uds_table.item(2,0).data(Qt.UserRole)['address']=='default'
    # A second click follows the newly inserted selection.
    page.uds_request.setText('22 F1 CC');page.uds_add_btn.click()
    assert page.uds_table.item(2,1).text()=='22 F1 CC'
    assert output.row()==3


def test_add_without_selected_row_appends(page):
    page.uds_table.clearSelection()
    page.uds_request.setText('22 F1 BB');page.uds_add_btn.click()
    assert page.uds_table.item(3,1).text()=='22 F1 BB'


def test_switching_rows_saves_previous_draft(page):
    page.load_uds_row(0)
    page.uds_request.setText('22 F1 AA')
    page.load_uds_row(2)
    assert page.uds_table.item(0,1).text()=='22 F1 AA'
    assert page._uds_edit_row()==2
    assert page.uds_request.text()=='22 F1 92'


def test_output_checkbox_does_not_open_editor_and_security_routes_to_dll(page):
    page.uds_table.cellClicked.emit(0,6)
    assert page.uds_tabs.currentIndex()==2
    assert page._uds_edit_item is None
    page._append_uds_table_row('DLL','@SECURITY','')
    page.uds_table.cellClicked.emit(3,1)
    assert page.uds_tabs.currentIndex()==1
    assert page._uds_edit_item is None


def test_confirm_dialog_commits_unsaved_row_and_roundtrips(page):
    page.load_uds_row(1)
    page.uds_request.setText('22 F1 AA')
    page.uds_result_expected.setText('Value')
    page.accept()
    assert page.result()==QDialog.Accepted
    saved=page.get_data()
    restored=StepDialog(step_data=saved)
    try:
        assert restored.uds_table.rowCount()==3
        assert restored.uds_table.item(1,1).text()=='22 F1 AA'
        assert restored.uds_table.item(1,5).text()=='Value'
    finally:restored.close()
