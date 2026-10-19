from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication,QMessageBox
from ui.tabs.settings_tab import SettingsTab
from ui.main_window import MainWindow
from core.report_settings import validate_settings

app=QApplication.instance() or QApplication([])

def test_settings_emit_independent_report_and_mes_directories(tmp_path):
    page=SettingsTab()
    try:
        page.set_report_settings(dict(report_root_dir=str(tmp_path),mes_report_dir=str(tmp_path/'mes'),report_equipment_no='EQ',report_station_name='APP'))
        values=[];page.report_save_requested.connect(values.append);page.btn_save_reports.click()
        assert values[0]['report_root_dir']==str(tmp_path) and values[0]['report_equipment_no']=='EQ'
        assert values[0]['mes_report_dir']==str(tmp_path/'mes')
    finally:page.close()

def test_settings_validation_and_busy_guard_do_not_save(tmp_path,monkeypatch):
    with pytest.raises(ValueError):validate_settings(dict(report_root_dir='relative/path'))
    with pytest.raises(ValueError):validate_settings(dict(report_root_dir=str(tmp_path),mes_report_dir='relative'))
    assert validate_settings(dict(report_root_dir=str(tmp_path)))['mes_report_dir']==''
    messages=[];monkeypatch.setattr(QMessageBox,'warning',lambda *args:messages.append(args))
    page=SettingsTab()
    try:
        fake=SimpleNamespace(tab_overview=SimpleNamespace(_run_busy=lambda:True),db_manager=Mock(),tab_settings=page)
        MainWindow._on_settings_report_save(fake,dict(report_root_dir=str(tmp_path)))
        fake.db_manager.save_sys_config.assert_not_called();assert messages
    finally:page.close()

def test_recipe_report_fields_survive_editor_and_runtime_snapshot(tmp_path):
    from data.db_manager import DBManager
    from ui.tabs.config_tab import ConfigTab
    from ui.tabs.overview_tab import OverviewTab
    db=DBManager(str(tmp_path/'test.db'))
    page=ConfigTab(db)
    try:
        page.report_project_no.setText('AP-DJ2403');page.report_model_name.setText('MODEL_APP')
        data=page._collect_recipe_data('R_APP')
        assert data['project_no']=='AP-DJ2403' and data['model_name']=='MODEL_APP'
        fake=SimpleNamespace(current_recipe_name='R_APP',current_recipe_data=data,step_tree=page.step_tree)
        snapshot=OverviewTab.serialize_tree_to_dict(fake)
        assert snapshot['project_no']=='AP-DJ2403' and snapshot['model_name']=='MODEL_APP'
    finally:page.close();db.close()
