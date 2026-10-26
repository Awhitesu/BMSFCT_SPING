from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

from PySide6.QtWidgets import QApplication, QTableWidget, QPushButton
from PySide6.QtGui import QFont, QFontDatabase

from core.generic_mes import load_file, save_file, config_path
from ui.dialogs.generic_mes_dialog import GenericMesDialog
from ui.main_window import MainWindow
from ui.tabs.settings_tab import SettingsTab
from ui.tabs.overview_tab import OverviewTab
from test_generic_mes import settings, query_body


app = QApplication.instance() or QApplication([])


def test_configuration_button_and_master_switch_do_not_load_file_when_disabled():
    page = SettingsTab()
    config_requests, switches = [], []
    try:
        with patch('core.generic_mes.load_file', side_effect=AssertionError('disabled must not load')):
            page.generic_mes_config_requested.connect(lambda: config_requests.append(True))
            page.mes_switch_save_requested.connect(switches.append)
            page.set_mes_settings({'generic_mes_enabled': True})
            assert switches == []
            page.btn_config_generic_mes.click()
            page.chk_generic_mes.setChecked(False)
            host = SimpleNamespace(tab_overview=SimpleNamespace(_run_busy=lambda: False, set_generic_mes_enabled=Mock()), tab_settings=page,
                                   db_manager=SimpleNamespace(db_path='unused.db', save_sys_config=Mock(return_value=True), load_sys_config=lambda: {}))
            with patch('PySide6.QtWidgets.QMessageBox.information'):
                MainWindow._on_settings_mes_switch_save(host, switches[0])
        assert config_requests == [True]
        assert switches == [{'generic_mes_enabled': False}]
        host.db_manager.save_sys_config.assert_called_once_with({'generic_mes_enabled': False})
        assert not hasattr(page, 'btn_save_generic_mes')
    finally:
        page.close()


def test_popup_roundtrips_all_editable_parameters_into_independent_config(tmp_path):
    path = tmp_path/'config'/'generic_mes.json'
    dialog = GenericMesDialog(path)
    try:
        editor = dialog.settings
        assert editor.query_url.text().endswith('/checkBmsSnInfo') and editor.upload_url.text().endswith('/checkBmsProc')
        editor.query_url.setText('http://127.0.0.1/custom/query')
        editor.upload_url.setText('http://127.0.0.1/custom/upload')
        editor.station_id.setText('CUSTOM-88')
        editor.timeout.setValue(7.5)
        editor.max_check_times.setValue(7)
        editor.upload_attempts.setValue(2)
        with patch('ui.dialogs.generic_mes_dialog.QMessageBox.information'):
            editor.save_button.click()
        saved = load_file(path)
        assert saved == editor.values()
        assert not hasattr(editor, 'check_type') and not hasattr(editor, 'check_item')
        assert not hasattr(editor, 'detail_format')
        assert 'generic_mes_check_type' not in saved
        assert 'generic_mes_check_item' not in saved
        assert 'generic_mes_detail_format' not in saved
        assert saved['generic_mes_max_check_times'] == 7
        assert saved['generic_mes_upload_attempts'] == 2
        assert 'generic_mes_item_mapping' not in saved
        assert not editor.findChildren(QTableWidget)
        assert not (path.parent/'sys_config.json').exists()
        reopened = GenericMesDialog(path)
        try:
            assert reopened.settings.values() == saved
        finally:
            reopened.close()
    finally:
        dialog.close()


def test_enable_missing_file_is_rejected_without_saving(tmp_path):
    page = SettingsTab()
    host = SimpleNamespace(tab_overview=SimpleNamespace(_run_busy=lambda: False), tab_settings=page,
                           db_manager=SimpleNamespace(db_path=str(tmp_path/'app.db'), save_sys_config=Mock(), load_sys_config=lambda: {}))
    try:
        with patch('PySide6.QtWidgets.QMessageBox.warning') as warning:
            MainWindow._on_settings_mes_switch_save(host, {'generic_mes_enabled': True})
        warning.assert_called_once()
        host.db_manager.save_sys_config.assert_not_called()
        assert not page.chk_generic_mes.isChecked()
    finally:
        page.close()


def test_enable_ruineng_disables_legacy_without_replacing_its_parameters(tmp_path):
    cfg = settings()
    save_file(cfg, config_path(tmp_path/'test.db'))
    current = {'mes_precheck_enabled': True, 'mes_url': 'http://legacy/check'}
    def save(values):
        current.update(values)
        return True
    page = SettingsTab()
    host = SimpleNamespace(tab_overview=SimpleNamespace(_run_busy=lambda: False, set_generic_mes_enabled=Mock()),
        tab_settings=page, db_manager=SimpleNamespace(db_path=str(tmp_path/'test.db'),
            save_sys_config=save, load_sys_config=lambda: current))
    try:
        page.set_mes_settings(current)
        page.mes_switch_save_requested.connect(lambda change: MainWindow._on_settings_mes_switch_save(host, change))
        page.edit_mes_url.setText('UNSAVED')
        with patch('PySide6.QtWidgets.QMessageBox.information'):
            page.chk_generic_mes.setChecked(True)
        assert current == {'mes_precheck_enabled': False, 'mes_url': 'http://legacy/check', 'generic_mes_enabled': True}
        assert page.chk_generic_mes.isChecked() and not page.chk_mes_precheck.isChecked()
        assert page.edit_mes_url.text() == 'UNSAVED'
        # Switch back using saved legacy parameters, never the unsaved form input.
        current['mes_equipment_no'] = 'LEGACY-1'
        page.chk_mes_precheck.setChecked(True)
        assert not page.chk_generic_mes.isChecked()
        assert current['mes_precheck_enabled'] and not current['generic_mes_enabled']
        assert page.edit_mes_url.text() == 'UNSAVED'
    finally:
        page.close()


def test_query_information_is_read_only_scoped_to_sn_and_cleared_on_new_scan():
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    try:
        assert page.ruineng_panel.isHidden()
        page.set_generic_mes_enabled(True)
        assert not page.ruineng_panel.isHidden()
        page._show_mes_query_result({'_generic_mes_result': dict(passed=True, sn='SN', response=query_body())})
        assert page.mes_info_fields['checkTimes'].text() == '1'
        assert page.mes_info_fields['orderId'].text() == 'ORDER-1'
        assert all(field.isReadOnly() for field in page.mes_info_fields.values())
        page.txt_sn.setText('NEXT-SN')
        assert all(field.text() == '—' for field in page.mes_info_fields.values())
        page._show_mes_query_result({'_generic_mes_result': dict(passed=False, sn='NEXT-SN', response=query_body('OTHER'))})
        assert all(field.text() == '—' for field in page.mes_info_fields.values())
        page._show_mes_query_result({'_generic_mes_result': dict(passed=False, sn='NEXT-SN', response='bad response')})
    finally:
        page.close()


def test_ruineng_panel_fits_between_statistics_and_sn(tmp_path):
    for name in ('msyh.ttc', 'msyhbd.ttc'):
        font_path = Path('C:/Windows/Fonts') / name
        if font_path.exists():
            QFontDatabase.addApplicationFont(str(font_path))
    original_font, original_style = app.font(), app.styleSheet()
    app.setFont(QFont('Microsoft YaHei', 10))
    app.setStyleSheet(Path('resources/style.qss').read_text(encoding='utf-8'))
    with patch.object(OverviewTab, 'load_default_or_first_recipe', lambda self: None):
        page = OverviewTab()
    page.stop_monitor()
    page.set_generic_mes_enabled(True)
    page._show_mes_query_result({'_generic_mes_result': dict(passed=True, sn='SN', response=query_body())})
    artifacts = Path('logs/ruineng_mes')
    artifacts.mkdir(parents=True, exist_ok=True)
    try:
        for width, height in ((1920, 950), (1366, 768), (1280, 720), (1024, 600)):
            page.resize(width, height)
            page.show()
            for _ in range(5):
                app.processEvents()
            widgets = (page.yield_stats_panel, page.ruineng_panel, page.txt_sn, page.btn_run_test)
            for first, second in zip(widgets, widgets[1:]):
                assert first.mapTo(page.right_panel, first.rect().bottomLeft()).y() < second.mapTo(page.right_panel, second.rect().topLeft()).y()
            assert all(field.width() >= 40 for field in page.mes_info_fields.values())
            assert not page._layout_scroll.horizontalScrollBar().isVisible()
            if width >= 1280:
                assert not page._layout_scroll.verticalScrollBar().isVisible()
            page.grab().save(str(artifacts/f'overview_{width}.png'))
    finally:
        page.close()
        app.setFont(original_font)
        app.setStyleSheet(original_style)


def test_initial_incomplete_parameters_are_editable_but_cannot_enable(tmp_path):
    import json
    path = config_path(tmp_path/'test.db')
    path.parent.mkdir()
    path.write_text(json.dumps({'generic_mes_station_id': '', 'generic_mes_check_type': ''}), encoding='utf-8')
    with patch('ui.dialogs.generic_mes_dialog.QMessageBox.warning') as warning:
        editor = GenericMesDialog(path)
    try:
        warning.assert_not_called()
        assert editor.settings.station_id.text() == ''
        assert not hasattr(editor.settings, 'check_type')
        with patch('ui.dialogs.generic_mes_dialog.QMessageBox.warning') as warning:
            editor.settings.save_button.click()
        warning.assert_called_once()
    finally:
        editor.close()


def test_switch_write_failure_rolls_back_both_controls_without_recursive_save(tmp_path):
    page = SettingsTab()
    current = {'mes_precheck_enabled': True, 'generic_mes_enabled': False}
    page.set_mes_settings(current)
    writes = Mock(return_value=False)
    host = SimpleNamespace(tab_overview=SimpleNamespace(_run_busy=lambda: False), tab_settings=page,
        db_manager=SimpleNamespace(db_path=str(tmp_path/'test.db'), save_sys_config=writes, load_sys_config=lambda: current))
    page.mes_switch_save_requested.connect(lambda change: MainWindow._on_settings_mes_switch_save(host, change))
    page.edit_mes_equipment.setText('未保存的参数')
    try:
        with patch('PySide6.QtWidgets.QMessageBox.warning') as warning, \
             patch('PySide6.QtWidgets.QMessageBox.information') as success:
            page.chk_mes_precheck.setChecked(False)
        writes.assert_called_once_with({'mes_precheck_enabled': False})
        warning.assert_called_once()
        success.assert_not_called()
        assert page.chk_mes_precheck.isChecked() and not page.chk_generic_mes.isChecked()
        assert page.edit_mes_equipment.text() == '未保存的参数'
    finally:
        page.close()


def test_both_switches_save_immediately_and_only_write_switches(tmp_path):
    save_file(settings(), config_path(tmp_path/'test.db'))
    page = SettingsTab()
    current = dict(mes_precheck_enabled=False, generic_mes_enabled=False,
                   mes_url='http://legacy/check', mes_equipment_no='JH-1', mes_timeout=10)
    writes = []
    def save(change):
        writes.append(dict(change))
        current.update(change)
        return True
    host = SimpleNamespace(tab_overview=SimpleNamespace(_run_busy=lambda: False, set_generic_mes_enabled=Mock()),
        tab_settings=page, db_manager=SimpleNamespace(db_path=str(tmp_path/'test.db'), save_sys_config=save,
                                                    load_sys_config=lambda: current))
    page.mes_switch_save_requested.connect(lambda change: MainWindow._on_settings_mes_switch_save(host, change))
    try:
        page.set_mes_settings(current)
        assert writes == []
        page.edit_mes_url.setText('尚未保存')
        with patch('PySide6.QtWidgets.QMessageBox.information') as popup:
            page.chk_mes_precheck.setChecked(True)
            assert current['mes_precheck_enabled'] and not current['generic_mes_enabled']
            page.chk_generic_mes.setChecked(True)
            assert current['generic_mes_enabled'] and not current['mes_precheck_enabled']
            page.chk_generic_mes.setChecked(False)
            assert not current['generic_mes_enabled'] and not current['mes_precheck_enabled']
        popup.assert_not_called()
        assert writes == [
            {'mes_precheck_enabled': True, 'generic_mes_enabled': False},
            {'generic_mes_enabled': True, 'mes_precheck_enabled': False},
            {'generic_mes_enabled': False}]
        assert current['mes_url'] == 'http://legacy/check'
        assert page.edit_mes_url.text() == '尚未保存'
    finally:
        page.close()


def test_saving_legacy_parameters_does_not_change_either_switch():
    current = {'mes_precheck_enabled': False, 'generic_mes_enabled': True}
    page = SettingsTab()
    page.set_mes_settings(current)
    writes = Mock(return_value=True)
    host = SimpleNamespace(tab_overview=SimpleNamespace(_run_busy=lambda: False, _scan_pending=False),
        tab_settings=page, db_manager=SimpleNamespace(load_sys_config=lambda: current, save_sys_config=writes))
    requests = []
    page.mes_save_requested.connect(requests.append)
    try:
        page.edit_mes_equipment.setText('JH-1')
        page.btn_save_mes.click()
        with patch('PySide6.QtWidgets.QMessageBox.information'):
            MainWindow._on_settings_mes_save(host, requests[0])
        parameters = writes.call_args.args[0]
        assert 'mes_precheck_enabled' not in parameters and 'generic_mes_enabled' not in parameters
        assert parameters['mes_equipment_no'] == 'JH-1'
        assert page.chk_generic_mes.isChecked() and not page.chk_mes_precheck.isChecked()
    finally:
        page.close()
