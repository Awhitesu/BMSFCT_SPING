"""Group freshness, isolated channels, condition changes and selected-only debug reads."""
from types import SimpleNamespace
from unittest.mock import patch
import threading
import time

import pytest
from PySide6.QtWidgets import QApplication
from core.dbc_signals import DBCMonitor,decode_step,encode_step,execute_read
from core.engine import ChannelWorker,SubStep,SubStepType,TestStep as Step,StepType
from ui.widgets.dbc_forms import DBCStepForm
from test_dbc_signals import asset,config,Driver,until

app=QApplication.instance() or QApplication([])


@pytest.fixture
def session(asset):
    driver=Driver();monitor=DBCMonitor();monitor.configure(asset,driver)
    timers=[];workers=[]
    def later(delay,action):
        timer=threading.Timer(delay,action);timers.append(timer);timer.start()
    def make(configs,physical=None):
        steps=[]
        for index,c in enumerate(configs):
            parent=Step(f'item {index}',StepType.CUSTOM)
            sub=SubStep(SubStepType.CAN_DBC,dict(name='read',type='CANDBC交互',device='CANDBC 交互',
                params=encode_step(c)+f' / PCH:{physical[index] if physical else 1}',is_judgment=True))
            parent.add_sub_step(sub);steps.append(parent)
        worker=ChannelWorker(1,steps,device_manager=SimpleNamespace(pcan=driver,dbc_monitor=monitor))
        worker.is_running=True;workers.append(worker)
        return worker
    yield driver,monitor,later,make
    for timer in timers:timer.join(1)
    for worker in workers:worker.is_running=False
    monitor.stop()


def grouped(asset,signal='A',name='voltage',**values):
    return dict(config(asset,signal,'Multiplex'),group=name,**values)


def emit(driver,mux,value,interface=None):
    driver.emit(258,bytes([mux,value])+b'\x00'*6,interface)


def run(worker,index):
    worker.current_step_index=index
    worker.current_sub_step_index=0
    return worker._execute_sub_step_logic(worker.steps[index].sub_steps[0])


def wait_form(form):
    deadline=time.monotonic()+2
    while not form._loaded:
        assert time.monotonic()<deadline
        app.processEvents();time.sleep(.001)


def test_group_ignores_pre_epoch_values_then_keeps_the_saved_snapshot(asset,session):
    driver,monitor,later,make=session
    emit(driver,0,10);emit(driver,1,11)
    until(lambda:any(k[-1]=='B' for k in monitor.snapshot()))
    worker=make([grouped(asset),grouped(asset,'B',variable='RESULT_B')])
    later(.02,lambda:emit(driver,0,30));later(.05,lambda:emit(driver,1,40))
    assert run(worker,0)==(True,30)
    context=worker._dbc_group_context
    assert len(context['keys'])==2
    with patch.object(worker.cancel_event,'wait',side_effect=AssertionError('fresh cached value should not wait')):
        assert run(worker,1)==(True,40)
    assert worker.variables['RESULT_B']==40
    emit(driver,1,50)
    until(lambda:monitor.read(grouped(asset,'B'),driver.channel)['value']==50)
    assert run(worker,1)==(True,40)  # Background updates do not alter the saved snapshot.
    assert worker._dbc_group_context is context


def test_missing_group_signal_times_out_and_publishes_no_partial_result(asset,session):
    driver,monitor,later,make=session
    worker=make([dict(grouped(asset),timeout=.06,variable='RESULT_A'),dict(grouped(asset,'B'),timeout=.06)])
    later(.01,lambda:emit(driver,0,30))
    ok,error=run(worker,0)
    assert not ok and '刷新超时' in error and 'Multiplex.B' in error
    assert 'values' not in worker._dbc_group_context and 'RESULT_A' not in worker.variables
    # Continuing after a failed acquisition must not start partial numeric judgments.
    emit(driver,1,40)
    assert not run(worker,1)[0]


def test_cancellation_interrupts_group_refresh(asset,session):
    _,_,later,make=session
    worker=make([grouped(asset)])
    later(.02,worker.cancel_event.set)
    ok,error=run(worker,0)
    assert not ok and '取消' in error and 'values' not in worker._dbc_group_context


@pytest.mark.parametrize('skip_kind',['unchecked','comment_parent','comment_child'])
def test_selected_only_debug_waits_only_for_the_first_actually_executed_signal(asset,session,skip_kind):
    driver,_,later,make=session
    worker=make([grouped(asset),grouped(asset,'B')])
    if skip_kind=='unchecked':worker.steps[0].skip_runtime=True
    elif skip_kind=='comment_parent':worker.steps[0].name='#disabled'
    else:worker.steps[0].sub_steps[0].params['name']='#disabled'
    later(.02,lambda:emit(driver,1,40))
    assert run(worker,1)==(True,40)
    assert len(worker._dbc_group_context['keys'])==1


def test_each_explicit_group_starts_a_new_acquisition_even_with_the_same_name(asset,session):
    driver,_,later,make=session
    worker=make([grouped(asset,group_role='single'),grouped(asset,group_role='single')])
    later(.02,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    first_epoch=worker._dbc_group_context['since']
    later(.02,lambda:emit(driver,0,50))
    assert run(worker,1)==(True,50)
    assert worker._dbc_group_context['since']>first_epoch


def test_same_group_on_multiple_channels_waits_for_each_channel_independently(asset,session):
    driver,_,later,make=session
    driver.channel='ZLG_CAN1';driver.bus=SimpleNamespace(enabled_channels={0,1})
    worker=make([grouped(asset),grouped(asset)],physical=[1,2])
    later(.02,lambda:emit(driver,0,30,'ZLG_CAN1'))
    later(.05,lambda:emit(driver,0,40,'ZLG_CAN2'))
    assert run(worker,0)==(True,30)
    assert run(worker,1)==(True,40)
    assert len(worker._dbc_group_context['keys'])==2


def test_saved_group_value_remains_valid_after_100_seconds(asset,session):
    driver,_,later,make=session
    worker=make([dict(grouped(asset),timeout=.08,max_age=.05)])
    later(.01,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    captured=worker._dbc_group_context['captured']
    with patch('core.dbc_signals.time',SimpleNamespace(perf_counter=lambda:captured+100)):
        with patch.object(worker.cancel_event,'wait',side_effect=AssertionError('snapshot must not wait')):
            assert run(worker,0)==(True,30)


def test_reconnect_or_recipe_switch_during_sampling_fails_without_using_old_context(asset,session):
    driver,monitor,later,make=session
    worker=make([grouped(asset),grouped(asset,'B')])
    later(.01,lambda:emit(driver,0,30))
    later(.03,lambda:monitor.configure(asset,driver,context_key='another recipe'))
    ok,error=run(worker,0)
    assert not ok and '连接或配方已变化' in error and 'values' not in worker._dbc_group_context


def test_saved_snapshot_survives_later_bus_replacement(asset,session):
    driver,_,later,make=session
    worker=make([grouped(asset)])
    later(.01,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    context=worker._dbc_group_context
    driver.bus=object()
    later(.01,lambda:emit(driver,0,40))
    assert run(worker,0)==(True,30)
    assert worker._dbc_group_context is context


def test_duplicate_signal_is_sampled_once(asset,session):
    driver,_,later,make=session
    worker=make([grouped(asset),grouped(asset)])
    later(.01,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    assert len(worker._dbc_group_context['keys'])==1
    assert run(worker,1)==(True,30)


@pytest.mark.parametrize('counter',['step_retry_count','_retry_count'])
def test_parent_retest_and_child_retry_keep_the_same_snapshot(asset,session,counter):
    driver,_,later,make=session
    worker=make([grouped(asset)])
    later(.01,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    previous=worker._dbc_group_context['since']
    setattr(worker,counter,1)
    later(.01,lambda:emit(driver,0,50))
    assert run(worker,0)==(True,30)
    assert worker._dbc_group_context['since']==previous


def test_ordinary_latest_read_works_immediately_and_special_fresh_read_still_waits(asset,session):
    driver,monitor,_,_=session
    emit(driver,0,30);until(lambda:any(k[-1]=='A' for k in monitor.snapshot()))
    event=threading.Event();c=config(asset,'A','Multiplex')
    with patch.object(event,'wait',side_effect=AssertionError('valid cached data should return immediately')):
        assert execute_read(monitor,c,driver.channel,event.is_set,event.wait,lambda _:None)==(True,30)
    c.update(new_frame=True,timeout=.03)
    with pytest.raises(RuntimeError,match='超时'):
        execute_read(monitor,c,driver.channel,event.is_set,event.wait,lambda _:None)


def test_editor_defaults_to_latest_and_roundtrips_groups_without_changing_legacy_fresh_mode(asset):
    form=DBCStepForm(asset)
    try:
        assert not form.new_frame.isChecked() and form.interval.value()==.01
        legacy=dict(config(asset),new_frame=True)
        form.load(encode_step(legacy));wait_form(form)
        assert form.config()['new_frame'] is True
        form.group_role.setCurrentIndex(form.group_role.findData('start'))
        form.group.setText('condition A')
        saved=form.config()
        assert saved['group']=='condition A' and saved['group_role']=='start' and saved['new_frame'] is False
        assert not form.new_frame.isEnabled()
        form.load(encode_step(saved));wait_form(form)
        assert form.config()==saved
        form.group_role.setCurrentIndex(0);assert form.new_frame.isEnabled() and not form.group.text()
    finally:form.close()


@pytest.mark.parametrize('group',[123,'a\nb','a'*129])
def test_group_configuration_is_validated(asset,group):
    with pytest.raises(ValueError,match='采样组'):
        decode_step(encode_step(dict(config(asset),group=group)))


def test_per_step_fresh_and_group_fresh_cannot_both_be_enabled(asset):
    with pytest.raises(ValueError,match='同时'):
        decode_step(encode_step(dict(config(asset),group='A',new_frame=True)))


def test_explicit_range_inherits_middle_reads_and_survives_delay_and_write(asset,session):
    driver,monitor,later,make=session
    worker=make([grouped(asset,group_role='start'),
                 config(asset,'B','Multiplex',variable='MIDDLE'),
                 grouped(asset,group_role='end')])
    delay=Step('delay',StepType.CUSTOM)
    delay.add_sub_step(SubStep(SubStepType.WAIT,dict(params='100000',is_judgment=False)))
    write=Step('write',StepType.CUSTOM)
    write.add_sub_step(SubStep(SubStepType.CAN_SEND,dict(params='ID:0x100 / DATA:01 / PCH:1',is_judgment=False)))
    worker.steps[1:1]=[delay,write]
    later(.01,lambda:emit(driver,0,30));later(.03,lambda:emit(driver,1,40))
    assert run(worker,0)==(True,30)
    snapshot=worker._dbc_group_context
    assert len(snapshot['keys'])==2 and 'MIDDLE' not in worker.variables
    first_output=dict(worker.step_outputs[worker.steps[0].sub_steps[0].step_id])
    emit(driver,0,90);emit(driver,1,99)
    until(lambda:monitor.read(config(asset,'B','Multiplex'),driver.channel)['value']==99)
    with patch('core.engine.QTimer.singleShot') as timer:
        assert run(worker,1)[0]
        assert timer.call_args.args[0]==1000  # Existing countdown behavior remains intact.
    with patch.object(worker,'_send_can_step',return_value=True) as sender:
        assert run(worker,2)==(True,None)
        sender.assert_called_once()
    with patch('core.dbc_signals.time',SimpleNamespace(perf_counter=lambda:snapshot['captured']+100)):
        with patch.object(worker.cancel_event,'wait',side_effect=AssertionError('saved data must not wait')):
            assert run(worker,3)==(True,40)
            assert run(worker,4)==(True,30)
    assert worker.variables['MIDDLE']==40
    assert worker.step_outputs[worker.steps[0].sub_steps[0].step_id]==first_output
    assert worker._dbc_group_context is snapshot


def test_endpoint_excludes_later_ordinary_read(asset,session):
    driver,monitor,later,make=session
    worker=make([grouped(asset,group_role='start'),grouped(asset,'B',group_role='end'),
                 config(asset,'B','Multiplex')])
    later(.01,lambda:emit(driver,0,30));later(.03,lambda:emit(driver,1,40))
    assert run(worker,0)==(True,30)
    assert run(worker,1)==(True,40)
    emit(driver,1,50)
    until(lambda:monitor.read(config(asset,'B','Multiplex'),driver.channel)['value']==50)
    assert run(worker,2)==(True,50)
    assert worker._dbc_group_context is None


def test_snapshot_does_not_need_a_live_connection_after_capture(asset,session):
    driver,_,later,make=session
    worker=make([grouped(asset,group_role='start'),grouped(asset,'B',group_role='end')])
    later(.01,lambda:emit(driver,0,30));later(.03,lambda:emit(driver,1,40))
    assert run(worker,0)==(True,30)
    driver.is_connected=False
    assert run(worker,1)==(True,40)


def test_debug_subset_keeps_range_when_both_boundary_items_are_unchecked(asset,session):
    driver,_,later,make=session
    worker=make([grouped(asset,group_role='start'),config(asset,'B','Multiplex'),
                 grouped(asset,group_role='end')])
    worker.steps[0].skip_runtime=worker.steps[2].skip_runtime=True
    later(.01,lambda:emit(driver,1,40))
    assert run(worker,1)==(True,40)
    assert len(worker._dbc_group_context['keys'])==1


@pytest.mark.parametrize('refresh_expired',[True,False])
def test_collection_waits_for_expired_early_signal_or_times_out(asset,session,refresh_expired):
    from core.dbc_signals import refresh_group,signal_key
    driver,monitor,_,_=session
    configs=[dict(config(asset,s,'Multiplex'),max_age=.05,timeout=.15) for s in ('A','B')]
    clock=[10.0];tick=[0]

    def put(index,value):
        c=configs[index]
        with monitor._condition:
            monitor._values[signal_key(c,driver.channel)]=dict(value=value,raw=value,choice=str(value),
                received=clock[0],message=c['message'],signal=c['signal'],unit='',bus=driver.bus)

    def wait(seconds):
        clock[0]+=seconds;tick[0]+=1
        if tick[0]==1:put(0,30)
        if tick[0]==9:put(1,40)  # A is now expired; a partial snapshot is forbidden.
        if tick[0]==10 and refresh_expired:put(0,50)
        return False

    with patch('core.dbc_signals.time',SimpleNamespace(perf_counter=lambda:clock[0])):
        capture=lambda:refresh_group(monitor,[(c,driver.channel) for c in configs],'test',lambda:False,wait,lambda _:None)
        if refresh_expired:
            result=capture()
            assert result['values'][signal_key(configs[0],driver.channel)]['value']==50
            assert result['values'][signal_key(configs[1],driver.channel)]['value']==40
            assert all(v['age']<=.05 for v in result['values'].values())
        else:
            with pytest.raises(RuntimeError,match='刷新超时.*Multiplex.A'):
                capture()
        assert clock[0]>10.05  # max_age is not the overall sampling timeout.


def _items(configs):
    return [dict(name=f'item {i}',min='0',max='100',sub_steps=[dict(name='read',action='读取DBC信号',type='CANDBC交互',device='CANDBC 交互',
        params=encode_step(c)+' / PCH:1',is_judgment=True)]) for i,c in enumerate(configs)]


@pytest.mark.parametrize('roles,names,match',[
    (['start'],['A'],'缺少同名终点'),
    (['end'],['A'],'没有匹配'),
    (['start','end'],['A','B'],'没有匹配'),
    (['start','start','end'],['A','B','B'],'不能嵌套'),
])
def test_invalid_boundaries_are_rejected_before_execution(asset,roles,names,match):
    from core.dbc_groups import plan_groups
    items=_items([grouped(asset,name=n,group_role=r) for r,n in zip(roles,names)])
    with pytest.raises(ValueError,match=match):plan_groups(items)


def test_legacy_conversion_is_idempotent_and_preserves_non_group_fields(asset):
    from copy import deepcopy
    from core.dbc_groups import normalize_legacy_groups,plan_groups
    configs=[grouped(asset,variable='ONE'),grouped(asset,'B'),grouped(asset)]
    items=_items(configs);original=deepcopy(items)
    plan=plan_groups(items)
    assert items==original and len(plan)==3
    assert normalize_legacy_groups(items)==3
    assert normalize_legacy_groups(items)==0
    actual=[decode_step(i['sub_steps'][0]['params']) for i in items]
    assert [c.get('group_role') for c in actual]==['start',None,'end']
    assert [c.get('group') for c in actual]==['voltage',None,'voltage']
    for before,after in zip(configs,actual):
        assert {k:v for k,v in before.items() if k!='group'}=={k:v for k,v in after.items() if k not in ('group','group_role')}
    assert all(i['sub_steps'][0]['params'].endswith(' / PCH:1') for i in items)


def test_snapshot_group_rejects_dynamic_range_wait_but_ordinary_reads_keep_it(asset,session):
    from core.dbc_groups import plan_groups
    from core.read_until import attach
    items=_items([grouped(asset,group_role='single')])
    rc=dict(min=20,max=40,timeout=.2,interval=.01,consecutive=2)
    items[0]['sub_steps'][0]['params']=attach(items[0]['sub_steps'][0]['params'],rc)
    with pytest.raises(ValueError,match='固定快照.*范围等待'):plan_groups(items)
    driver,monitor,later,_=session
    later(.01,lambda:emit(driver,0,30));later(.04,lambda:emit(driver,0,31))
    assert execute_read(monitor,config(asset,'A','Multiplex'),driver.channel,
                        lambda:False,threading.Event().wait,lambda _:None,rc)==(True,31)


def test_editor_shows_boundaries_and_preserves_exact_saved_parameters(asset):
    from unittest.mock import Mock
    from PySide6.QtWidgets import QStyleOptionViewItem
    from core.dbc_signals import rebind_recipe_dbc,validate_recipe_dbc
    from ui.tabs.config_tab import ConfigTab
    data=dict(name='test',dbc=asset,can_config=dict(channel='PCAN_USBBUS1'),
              items=_items([grouped(asset),grouped(asset,'B'),grouped(asset)]))
    db=Mock();db.load_sys_config.return_value={};db.list_recipes.return_value=[]
    page=ConfigTab(db)
    try:
        page.load_recipe_to_tree(data)
        result=page._collect_recipe_data('test')
        validate_recipe_dbc(result)
        assert rebind_recipe_dbc(result)==0
        for i,role in [(0,'起点'),(2,'终点')]:
            node=page.step_tree.topLevelItem(i).child(0)
            saved=node.text(2)
            option=QStyleOptionViewItem()
            page.step_tree.itemDelegateForColumn(2).initStyleOption(option,page.step_tree.indexFromItem(node,2))
            assert f'【{role}】voltage' in option.text
            assert node.text(2)==saved==result['items'][i]['sub_steps'][0]['params']
    finally:page.close()


def test_boundary_error_selects_the_matching_editor_row(asset):
    from unittest.mock import Mock
    from ui.tabs.config_tab import ConfigTab
    data=dict(name='test',dbc=asset,can_config=dict(channel='PCAN_USBBUS1'),
              items=_items([config(asset,'A','Multiplex'),grouped(asset,group_role='start')]))
    db=Mock();db.load_sys_config.return_value={};db.list_recipes.return_value=[]
    page=ConfigTab(db)
    try:
        page.load_recipe_to_tree(data)
        with pytest.raises(ValueError,match='缺少同名终点'):page._collect_recipe_data('test')
        assert page.step_tree.currentItem() is page.step_tree.topLevelItem(1).child(0)
    finally:page.close()


def test_group_snapshot_uses_the_current_dbc_binding_after_id_replacement(asset,session):
    import hashlib
    driver,monitor,later,make=session
    worker=make([grouped(asset,group_role='start'),grouped(asset,'B',group_role='end')])
    text=asset['text'].replace('BO_ 258 Multiplex:','BO_ 400 Multiplex:')
    current=dict(asset,text=text,sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())
    monitor.configure(current,driver)
    later(.01,lambda:driver.emit(400,b'\x00\x1e'+b'\x00'*6))
    later(.03,lambda:driver.emit(400,b'\x01\x28'+b'\x00'*6))
    assert run(worker,0)==(True,30)
    assert run(worker,1)==(True,40)
    assert all(k[1]==400 for k in worker._dbc_group_context['keys'])


def test_full_sequence_judges_each_item_once_from_snapshot_after_delay(asset,session):
    driver,monitor,later,make=session
    worker=make([grouped(asset,group_role='start'),grouped(asset,'B',group_role='end')])
    worker.steps[0].min_limit=worker.steps[0].max_limit=30
    worker.steps[1].min_limit=worker.steps[1].max_limit=40
    delay=Step('delay',StepType.CUSTOM)
    delay.add_sub_step(SubStep(SubStepType.WAIT,dict(params='100',is_judgment=False)))
    worker.steps.insert(1,delay)
    finished=[];items=[]
    worker.test_finished.connect(lambda *args:finished.append(args))
    worker.step_finished.connect(lambda *args:items.append(args))
    later(.01,lambda:emit(driver,0,30));later(.03,lambda:emit(driver,1,40))
    later(.06,lambda:emit(driver,0,90));later(.06,lambda:emit(driver,1,99))
    worker.start()
    deadline=time.monotonic()+2
    while not finished:
        assert time.monotonic()<deadline
        app.processEvents();time.sleep(.001)
    assert finished==[(1,True)]
    assert len(items)==3 and all(args[3] for args in items)
    assert worker.step_outputs[worker.steps[0].sub_steps[0].step_id]['value']==30
    assert worker.step_outputs[worker.steps[2].sub_steps[0].step_id]['value']==40
    assert monitor.read(config(asset,'B','Multiplex'),driver.channel)['value']==99
