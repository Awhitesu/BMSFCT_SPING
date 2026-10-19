from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication,QWidget,QVBoxLayout,QTreeWidget,QTreeWidgetItem
from PySide6.QtCore import Qt
from core.variable_steps import encode,decode,execute,parse

app=QApplication.instance() or QApplication([])

def c(op='write',**kw):return dict(dict(operation=op,key='追溯码',source='last',input='text',segments=[],separator=''),**kw)

def test_keyed_write_read_and_missing_or_failed_source():
    w=SimpleNamespace(variables={},last_output=dict(ok=True,value='00123'),step_outputs={})
    assert execute(w,c())=='00123'
    assert execute(w,c('read'))=='00123'
    w.last_output=dict(ok=False,value='old')
    with pytest.raises(ValueError):execute(w,c())
    assert not w.variables
    with pytest.raises(ValueError):execute(w,c('read'))
    w.step_outputs['id']=dict(ok=True,value=12.3)
    assert execute(w,c(source='id'))==12.3
    assert decode(encode(c()))==c()

def test_visual_segments_ascii_hex_bcd_and_bounds():
    cfg=c(input='hex',segments=[dict(start=1,length=2,format='ASCII'),dict(start=3,length=1,format='BCD')])
    assert parse('41 42 26',cfg)=='AB26'
    assert parse('001234',c(segments=[dict(start=3,length=0,format='原值')]))=='1234'
    assert parse('01 02',c(input='hex',segments=[dict(start=1,length=2,format='UINT_LE')]))=='513'
    for value in ('41','41 42 FF'):
        with pytest.raises(ValueError):parse(value,cfg)

def test_all_step_outputs_and_separate_variable_engine():
    from core.engine import ChannelWorker,SubStep,SubStepType
    w=ChannelWorker(1,[],device_manager=SimpleNamespace());w.variables['input']='ABC123'
    source=SubStep(SubStepType.READ_VAR,dict(params='VAR:input'));source.step_id='source-id'
    assert w._execute_sub_step_logic(source)==(True,'ABC123')
    save=SubStep(SubStepType.READ_VAR,dict(params=encode(c(source='source-id'))))
    assert w._execute_sub_step_logic(save)==(True,'ABC123')
    read=SubStep(SubStepType.READ_VAR,dict(params=encode(c('read',segments=[dict(start=4,length=3,format='原值')]))))
    assert w._execute_sub_step_logic(read)==(True,'123')

def test_editor_catalog_stable_source_and_roundtrip():
    from ui.dialogs.step_dialog import StepDialog
    parent=QWidget();layout=QVBoxLayout(parent);parent.step_tree=QTreeWidget();parent.step_tree.setColumnCount(3);layout.addWidget(parent.step_tree)
    item=QTreeWidgetItem(parent.step_tree,['test']);sub=QTreeWidgetItem(item,['save','',encode(c())]);sub.setData(0,Qt.UserRole+20,'stable-id')
    d=StepDialog(parent)
    try:
        d.category_combo.setCurrentText('通用交互');d.device_combo.setCurrentText('读取变量')
        form=d.variable_step_form
        assert d.param_stack.currentWidget() is form
        assert form.key.findText('追溯码')>=0
        form.load(c('read'));data=d.get_data()
        restored=StepDialog(parent,step_data=data)
        try:assert decode(restored.get_data()['params'])==c('read')
        finally:restored.close()
    finally:d.close();parent.close()


def test_debug_editor_loads_runtime_value_and_blocks_other_recipe():
    from ui.dialogs.step_dialog import StepDialog
    parent=QWidget();parent.current_recipe_name='recipe'
    parent.test_engine=SimpleNamespace(workers={},debug_recipe_name='recipe',debug_variables={'追溯码':'AB261019XYZ'},
        debug_last_output=dict(ok=True,value='AB261019XYZ'),debug_step_outputs={})
    d=StepDialog(parent)
    try:
        f=d.variable_step_form;f.load(c('read'))
        assert f.sample.text()=='AB261019XYZ'
        assert 'AB261019XYZ' not in f.params()
        f.set_operation('write');assert f.sample.text()=='AB261019XYZ'
        parent.current_recipe_name='other';f.sample.clear();f.load_runtime()
        assert not f.sample.text()
    finally:d.close();parent.close()


def test_debug_finish_preserves_last_output_for_next_selected_run():
    from core.engine import TestEngine,ChannelWorker
    engine=TestEngine();w=ChannelWorker(1,[]);w._debug_variable_session=True
    w.last_output=dict(ok=True,value='previous-result');engine.workers[1]=w
    engine.stop_channel_test=lambda *args,**kwargs:None
    engine._on_worker_test_finished(1,True)
    assert engine.debug_last_output==dict(ok=True,value='previous-result')
    engine.workers.clear();engine.stop_all()


def test_multiple_drag_segments_combination_reorder_and_roundtrip():
    from ui.dialogs.step_dialog import StepDialog
    d=StepDialog()
    try:
        f=d.variable_step_form;f.key.setCurrentText('key');f.input.setCurrentIndex(1)
        f.sample.setText('01 02 03 41 42 43 44 12 34 45')
        for offset,length,kind in [(0,3,'HEX'),(3,4,'ASCII'),(7,2,'BCD'),(9,1,'ASCII')]:
            f.grid.set_selection(offset,length);f.append_selection(kind)
        cfg=f.config()
        assert len(cfg['segments'])==4
        assert parse(f.sample.text(),cfg)=='010203ABCD1234E'
        assert f.grid._blocks[0].toolTip()=='解析段：1'
        f.table.selectRow(3);f.move_segment(-1)
        assert parse(f.sample.text(),f.config())=='010203ABCDE1234'
        assert decode(f.params())==f.config()
    finally:d.close()
