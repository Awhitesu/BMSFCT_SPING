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


def test_group_ignores_pre_epoch_values_then_reuses_latest_values_without_another_cycle(asset,session):
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
    assert run(worker,1)==(True,50)  # Latest value, rather than a frozen copy of 40.
    assert worker._dbc_group_context is context


def test_missing_group_signal_times_out_and_publishes_no_partial_result(asset,session):
    driver,monitor,later,make=session
    worker=make([dict(grouped(asset),timeout=.06,variable='RESULT_A'),dict(grouped(asset,'B'),timeout=.06)])
    later(.01,lambda:emit(driver,0,30))
    ok,error=run(worker,0)
    assert not ok and '刷新超时' in error and 'Multiplex.B' in error
    assert worker._dbc_group_context is None and 'RESULT_A' not in worker.variables


def test_cancellation_interrupts_group_refresh(asset,session):
    _,_,later,make=session
    worker=make([grouped(asset)])
    later(.02,worker.cancel_event.set)
    ok,error=run(worker,0)
    assert not ok and '取消' in error and worker._dbc_group_context is None


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


@pytest.mark.parametrize('boundary',['different_group','non_dbc_step','ungrouped_read'])
def test_each_condition_boundary_requires_another_post_boundary_sample(asset,session,boundary):
    driver,_,later,make=session
    worker=make([grouped(asset),grouped(asset,name='next' if boundary=='different_group' else 'voltage')])
    later(.02,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    first_epoch=worker._dbc_group_context['since']
    if boundary=='non_dbc_step':
        other=SubStep(SubStepType.CALCULATE,dict(params='FORMULA:1',is_judgment=False))
        assert worker._execute_sub_step_logic(other)[0]
    elif boundary=='ungrouped_read':
        other=SubStep(SubStepType.CAN_DBC,dict(params=encode_step(config(asset,'A','Multiplex'))+' / PCH:1'))
        assert worker._execute_sub_step_logic(other)[0]
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


def test_expired_group_value_waits_and_cannot_be_accepted_from_the_old_cache(asset,session):
    driver,_,later,make=session
    worker=make([dict(grouped(asset),timeout=.08,max_age=.05)])
    later(.01,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    time.sleep(.06)
    ok,error=run(worker,0)
    assert not ok and '超时' in error


def test_reconnect_or_recipe_switch_during_sampling_fails_without_using_old_context(asset,session):
    driver,monitor,later,make=session
    worker=make([grouped(asset),grouped(asset,'B')])
    later(.01,lambda:emit(driver,0,30))
    later(.03,lambda:monitor.configure(asset,driver,context_key='another recipe'))
    ok,error=run(worker,0)
    assert not ok and '连接或配方已变化' in error and worker._dbc_group_context is None


def test_a_completed_group_is_refreshed_after_bus_replacement(asset,session):
    driver,_,later,make=session
    worker=make([grouped(asset)])
    later(.01,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    context=worker._dbc_group_context
    driver.bus=object()
    later(.01,lambda:emit(driver,0,40))
    assert run(worker,0)==(True,40)
    assert worker._dbc_group_context is not context


def test_duplicate_signal_is_sampled_once_and_range_wait_still_counts_distinct_reports(asset,session):
    from core.read_until import attach
    driver,_,later,make=session
    worker=make([grouped(asset),grouped(asset)])
    first=worker.steps[0].sub_steps[0]
    first.params['params']=attach(first.params['params'],dict(min=20,max=40,timeout=.15,interval=.01,consecutive=2))
    later(.01,lambda:emit(driver,0,30));later(.05,lambda:emit(driver,0,31))
    assert run(worker,0)==(True,31)
    assert len(worker._dbc_group_context['keys'])==1
    assert run(worker,1)==(True,31)


@pytest.mark.parametrize('counter',['step_retry_count','_retry_count'])
def test_parent_retest_and_child_retry_establish_a_new_sampling_epoch(asset,session,counter):
    driver,_,later,make=session
    worker=make([grouped(asset)])
    later(.01,lambda:emit(driver,0,30))
    assert run(worker,0)==(True,30)
    previous=worker._dbc_group_context['since']
    setattr(worker,counter,1)
    later(.01,lambda:emit(driver,0,50))
    assert run(worker,0)==(True,50)
    assert worker._dbc_group_context['since']>previous


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
        form.group.setText('condition A')
        saved=form.config()
        assert saved['group']=='condition A' and saved['new_frame'] is False
        assert not form.new_frame.isEnabled()
        form.load(encode_step(saved));wait_form(form)
        assert form.config()==saved
        form.group.clear();assert form.new_frame.isEnabled()
    finally:form.close()


@pytest.mark.parametrize('group',[123,'a\nb','a'*129])
def test_group_configuration_is_validated(asset,group):
    with pytest.raises(ValueError,match='采样组'):
        decode_step(encode_step(dict(config(asset),group=group)))


def test_per_step_fresh_and_group_fresh_cannot_both_be_enabled(asset):
    with pytest.raises(ValueError,match='同时'):
        decode_step(encode_step(dict(config(asset),group='A',new_frame=True)))
