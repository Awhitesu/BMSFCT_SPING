from copy import deepcopy
import hashlib
import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from PySide6.QtWidgets import QApplication, QLabel
from core.dbc_signals import (database, decode_step, encode_step, execute_read, resolve_step,
                              rebind_recipe_dbc, validate_recipe_dbc, DBCMonitor)
from core.read_until import attach
from core.engine import ChannelWorker, SubStep, SubStepType
from ui.widgets.dbc_forms import DBCStepForm
from ui.tabs.config_tab import ConfigTab
from test_dbc_signals import asset, config, Driver, until

app = QApplication.instance() or QApplication([])


def changed_asset(asset, transform=lambda text:text + '\nCM_ "Updated file";\n'):
    text = transform(asset['text'])
    result = dict(name='replacement.dbc', text=text, sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
    database(result)
    return result


def relocated_asset(asset):
    return changed_asset(asset, lambda text:text.replace('BO_ 256 Status:', 'BO_ 400 Status:')
                         .replace('(0.1,0)', '(0.2,5)').replace('"V" BMS', '"mV" BMS'))


def recipe(asset, step):
    return dict(name='test', dbc=asset, can_config=dict(channel='PCAN_USBBUS1'),
                items=[dict(step_id='parent',name='[2.5V]单体电压 2',min='2498',max='2502',
                            sub_steps=[dict(step_id='child', name='custom read', action='读取DBC信号',
                                            type='CANDBC交互', device='CANDBC 交互',
                                            params=step,is_judgment=True,fail_strategy='忽略继续')])])


def wait_form(form):
    deadline = time.monotonic() + 3
    while not form._loaded:
        assert time.monotonic() < deadline
        app.processEvents()
        time.sleep(.001)


def test_changed_file_same_names_rebinds_new_id_metadata_and_preserves_all_settings(asset):
    old = config(asset,output='raw',variable='CELL_VALUE')
    old.update(timeout=3,interval=.1,max_age=2,new_frame=True)
    suffix = attach('placeholder', dict(min=2498,max=2502,timeout=10,interval=.1,consecutive=2)).split(' / ',1)[1]
    saved = encode_step(old)+' / '+suffix+' / PCH:2'
    data = recipe(relocated_asset(asset),saved)
    original = deepcopy(data)
    validate_recipe_dbc(data)
    assert data == original  # Validation itself remains read-only.
    assert rebind_recipe_dbc(data) == 1
    actual = data['items'][0]['sub_steps'][0]
    new = decode_step(actual['params'])
    assert new['sha256'] == data['dbc']['sha256'] and new['frame_id'] == 400 and new['unit'] == 'mV'
    for key in ('signal','message','variable','timeout','interval','max_age','new_frame'):
        assert new[key] == old[key]
    assert new['output'] == 'value'
    assert actual['params'].split(' / ',1)[1] == suffix+' / PCH:2'
    actual['params'] = saved
    assert data == original


def test_comment_only_dbc_change_does_not_invalidate_signal(asset):
    old = config(asset)
    new = changed_asset(asset)
    resolved = resolve_step(old,new)
    assert resolved == dict(old,sha256=new['sha256'])


def test_missing_signal_does_not_bind_same_named_signal_in_another_message(asset):
    new = changed_asset(asset,lambda text:text.replace(
        ' SG_ Voltage : 0|16@1+ (0.1,0) [0|6553.5] "V" BMS',''))
    with pytest.raises(ValueError, match='找不到信号 Status.Voltage'):
        resolve_step(config(asset),new)


def test_renamed_message_with_unique_signal_is_resolved_but_ambiguous_names_are_rejected(asset):
    new = changed_asset(asset,lambda text:text.replace('BigEndian:', 'Renamed:'))
    assert resolve_step(config(asset,'Current','BigEndian'),new)['message'] == 'Renamed'
    ambiguous = dict(config(asset),message='Removed',frame_id=999)
    with pytest.raises(ValueError,match='多个同名信号'):
        resolve_step(ambiguous,new)


def test_rebinding_is_atomic_when_another_signal_is_missing_and_integrity_stays_checked(asset):
    data = recipe(changed_asset(asset),encode_step(config(asset)))
    bad = deepcopy(data['items'][0])
    bad['name'] = 'missing'
    c = dict(config(asset),signal='DoesNotExist')
    bad['sub_steps'][0]['params'] = encode_step(c)
    data['items'].append(bad)
    original = deepcopy(data)
    with pytest.raises(ValueError,match='missing'):
        rebind_recipe_dbc(data)
    assert data == original
    damaged = deepcopy(data['dbc']);damaged['text'] += '\n'
    with pytest.raises(ValueError,match='校验'):
        resolve_step(config(asset),damaged)


def test_replacement_enum_removal_does_not_silently_change_legacy_output(asset):
    new = changed_asset(asset,lambda text:text.replace('VAL_ 256 State 0 "Off" 1 "On";',''))
    with pytest.raises(ValueError,match='已无枚举定义'):
        resolve_step(config(asset,'State',output='choice'),new)


def test_legacy_raw_setting_reads_actual_value_and_ranges_use_the_same_value_as_debug(asset):
    new = relocated_asset(asset)
    driver = Driver();monitor = DBCMonitor();monitor.configure(new,driver)
    cancel = threading.Event()
    try:
        msg = database(new).get_message_by_name('Status')
        driver.emit(400,msg.encode(dict(Voltage=5005,Temp=0,State=0)))
        until(lambda:len(monitor.snapshot())==3)
        cached = monitor.snapshot()[(driver.channel,400,False,'Voltage')]
        assert cached['raw'] == 25000 and cached['value'] == 5005
        old = config(asset,output='raw')
        # Old raw settings are upgraded, and the DBC scale/offset is applied exactly once.
        assert execute_read(monitor,old,driver.channel,cancel.is_set,cancel.wait,lambda _:None,
                            dict(min=5004,max=5006,timeout=1,interval=.01,consecutive=1)) == (True,5005)
        old['output'] = 'value'
        assert execute_read(monitor,old,driver.channel,cancel.is_set,cancel.wait,lambda _:None) == (True,5005)
    finally: monitor.stop()


def test_actual_signed_motorola_and_existing_enum_modes(asset):
    driver = Driver();monitor = DBCMonitor();monitor.configure(asset,driver)
    cancel = threading.Event()
    try:
        msg = database(asset).get_message_by_name('BigEndian')
        driver.emit(257,msg.encode(dict(Current=-3.5)))
        status = database(asset).get_message_by_name('Status')
        driver.emit(256,status.encode(dict(Voltage=10,Temp=0,State=1)))
        until(lambda:len(monitor.snapshot())==4)
        assert execute_read(monitor,config(asset,'Current','BigEndian',output='raw'),driver.channel,
                            cancel.is_set,cancel.wait,lambda _:None) == (True,-3.5)
        assert execute_read(monitor,config(asset,'State',output='choice'),driver.channel,
                            cancel.is_set,cancel.wait,lambda _:None) == (True,'On')
    finally: monitor.stop()


def test_editor_removes_output_row_and_loads_old_binding_against_new_dbc(asset):
    new = relocated_asset(asset)
    form = DBCStepForm(new)
    try:
        assert not hasattr(form,'output')
        assert not any(label.text() == '输出：' for label in form.findChildren(QLabel))
        old = config(asset,output='value',variable='CELL_VALUE')
        form.load(encode_step(old));wait_form(form)
        saved = form.config()
        assert saved['output'] == 'value' and saved['frame_id'] == 400
        assert saved['variable'] == 'CELL_VALUE' and saved['sha256'] == new['sha256']
        assert saved['timeout'] == old['timeout'] and saved['new_frame'] == old['new_frame']
        assert '实际值单位 mV' in form.info.text()
    finally: form.close()


def test_recipe_editor_collect_rebinds_all_steps_and_synchronizes_tree(asset):
    db=Mock();db.load_sys_config.return_value={};db.list_recipes.return_value=[]
    editor=ConfigTab(db)
    original=recipe(asset,encode_step(config(asset,output='raw')))
    try:
        editor.load_recipe_to_tree(original)
        new = relocated_asset(asset)
        editor.dbc_import.set_asset(new)
        saved=editor._collect_recipe_data('test')
        actual=saved['items'][0]['sub_steps'][0]
        assert decode_step(actual['params'])['sha256']==new['sha256']
        assert decode_step(actual['params'])['frame_id']==400
        assert editor.step_tree.topLevelItem(0).child(0).text(2)==actual['params']
        assert (saved['items'][0]['name'],saved['items'][0]['min'],saved['items'][0]['max']) == (
            original['items'][0]['name'],'2498','2502')
        assert actual['step_id']=='child' and actual['name']=='custom read'
    finally: editor.close()


@pytest.mark.parametrize('output',['value','raw'])
def test_engine_stores_actual_value_in_requested_variable_including_legacy_raw_settings(asset,output):
    new=relocated_asset(asset)
    driver=Driver();monitor=DBCMonitor();monitor.configure(new,driver)
    worker=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=driver,dbc_monitor=monitor))
    worker.is_running=True
    try:
        driver.emit(400,database(new).get_message_by_name('Status').encode(dict(Voltage=5005,Temp=0,State=0)))
        until(lambda:bool(monitor.snapshot()))
        old=config(asset,output=output,variable='CELL_VALUE')
        step=SubStep(SubStepType.CAN_DBC,dict(params=encode_step(old)+' / PCH:1',is_judgment=True))
        assert worker._execute_sub_step_logic(step)==(True,5005)
        assert worker.variables['CELL_VALUE']==5005
    finally: worker.is_running=False;monitor.stop()
