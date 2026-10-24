import copy
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from ui.tabs.config_tab import ConfigTab
from ui.tabs.overview_tab import OverviewTab
from ui.dialogs.step_dialog import StepDialog
from ui.widgets.dbc_forms import DBCMonitorTab
from core.dbc_signals import DBCMonitor,encode_step,decode_step
from core.engine import ChannelWorker,SubStep,SubStepType
from test_dbc_signals import asset,Driver,config,until

app=QApplication.instance() or QApplication([])


def test_recipe_dbc_optional_and_snapshot_roundtrip(asset):
    db=Mock();db.load_sys_config.return_value={};db.list_recipes.return_value=[]
    page=ConfigTab(db)
    try:
        assert page._collect_recipe_data('NO_DBC')['dbc'] is None
        page.dbc_import.set_asset(asset)
        data=page._collect_recipe_data('WITH_DBC')
        assert data['dbc']==asset
        fake=SimpleNamespace(current_recipe_name='WITH_DBC',current_recipe_data=data,step_tree=page.step_tree)
        assert OverviewTab.serialize_tree_to_dict(fake)['dbc']==asset
        dialog=StepDialog(page)
        try:
            dialog.category_combo.setCurrentText('报文交互');dialog.device_combo.setCurrentText('CANDBC 交互')
            for _ in range(100):
                app.processEvents()
                if dialog.dbc_form._loaded:break
                __import__('time').sleep(.01)
            assert dialog.param_stack.currentWidget() is dialog.dbc_form
            dialog.cb_judgment.setChecked(True)
            step=dialog.get_data();assert step['type']=='CANDBC交互'
            assert decode_step(step['params'])['signal']=='Voltage'
            restored=StepDialog(page,step_data=step)
            try:
                for _ in range(100):
                    app.processEvents()
                    if restored.dbc_form._loaded:break
                    __import__('time').sleep(.01)
                assert restored.get_data()['params']==step['params']
            finally:restored.close()
            dialog.dbc_form.signal.setEditText('not a real signal')
            with pytest.raises(ValueError):dialog.get_data()
        finally:dialog.close()
        page.dbc_import.set_asset(None)
        assert page._collect_recipe_data('NO_DBC_AGAIN')['dbc'] is None
    finally:page.close()


@pytest.mark.parametrize('channel,physical,interface',[('PCAN_USBBUS1',1,'PCAN_USBBUS1'),('ZLG_CAN1',2,'ZLG_CAN2')])
def test_engine_reads_same_monitor_as_debug_without_can_tx(asset,channel,physical,interface):
    driver=Driver();driver.channel=channel;monitor=DBCMonitor();monitor.configure(asset,driver)
    manager=SimpleNamespace(pcan=driver,dbc_monitor=monitor)
    worker=ChannelWorker(1,[],device_manager=manager);worker.is_running=True
    debug=DBCMonitorTab(manager)
    try:
        c=config(asset);driver.emit(256,b'\x64\x00'+b'\x00'*6,interface)
        until(lambda:bool(monitor.snapshot()))
        step=SubStep(SubStepType.CAN_DBC,dict(device='CANDBC 交互',action='读取DBC信号',
            params=encode_step(c)+f' / PCH:{physical}',is_judgment=True))
        ok,value=worker._execute_sub_step_logic(step)
        assert ok and value==10
        assert value.unit == 'V'
        assert worker.current_step_results[-1]==(10,True)
        debug.show();app.processEvents();debug.channel.setCurrentText(interface);debug.refresh()
        row=next(i for i,r in enumerate(debug._rows) if r['signal']=='Voltage' and r['message']=='Status')
        assert float(debug.table.item(row,2).text())==10
        assert debug.table.item(row,6).text()=='有效'
        monitor.configure(None,driver);debug.refresh()
        assert debug.table.rowCount()==0
    finally:worker.is_running=False;debug.close();monitor.stop()


def test_switch_recipe_replaces_dbc_even_when_can_configuration_is_reused(asset):
    monitor=DBCMonitor();driver=Driver();manager=SimpleNamespace(dbc_monitor=monitor,pcan=driver)
    try:
        OverviewTab._apply_recipe_dbc(manager,dict(can_config=dict(channel=driver.channel),dbc=asset))
        assert monitor.asset==asset and len(driver.listeners)==1
        OverviewTab._apply_recipe_dbc(manager,dict(can_config=dict(channel=driver.channel),dbc=asset))
        assert len(driver.listeners)==1
        OverviewTab._apply_recipe_dbc(manager,dict(can_config={},dbc=None))
        assert monitor.asset is None and not driver.listeners
    finally:monitor.stop()
