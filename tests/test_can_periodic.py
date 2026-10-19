import threading
import time
from types import SimpleNamespace
from unittest.mock import Mock
import pytest
from PySide6.QtWidgets import QApplication
from core.can_periodic import PeriodicLists,encode,decode,validate_plan

app=QApplication.instance() or QApplication([])


def start(name='BMS'):
    return dict(operation='start',name=name,frames=[dict(id=0x123,data='01 02',period_ms=20,type=0),dict(id=0x456,data='03',period_ms=60,type=0)])


def stop(name='BMS'):return dict(operation='stop',name=name)


def plan(*rows):return dict(items=[dict(name='test',sub_steps=[dict(params=encode(row)) for row in rows])])


@pytest.mark.parametrize('rows',[(stop(),),(start(),start(),stop()),(start(),stop('wrong')),(start(),),(start(),stop(),stop())])
def test_order_pairing_rejects_bad_sequences(rows):
    with pytest.raises(ValueError):validate_plan(plan(*rows))


def test_pairing_and_disabled_or_selected_steps():
    validate_plan(plan(start(),start('other'),stop(),stop('other'),start(),stop()))
    p=plan(start(),stop());p['items'][0]['sub_steps'][0]['skip_runtime']=True
    with pytest.raises(ValueError):validate_plan(p)
    p['items'][0]['skip_runtime']=True;validate_plan(p)
    assert decode(encode(start()))==start()


def test_debug_allows_start_only_and_stop_of_actual_active_list():
    validate_plan(plan(start()),debug=True)
    validate_plan(plan(stop()),debug=True,active_names=['BMS'])
    with pytest.raises(ValueError):validate_plan(plan(stop()),debug=True)
    with pytest.raises(ValueError):validate_plan(plan(start()),debug=True,active_names=['BMS'])
    with pytest.raises(ValueError):validate_plan(plan(start()))


def test_completed_debug_worker_leaves_list_for_explicit_stop():
    from core.engine import ChannelWorker,TestEngine
    engine=TestEngine();d=driver()
    engine.debug_periodic_can.start(start(),d,1)
    w=ChannelWorker(1,[]);w.periodic_can=engine.debug_periodic_can;w._keep_debug_periodic=True;w.is_running=True
    try:
        w.run_next_step()
        assert 'BMS' in engine.debug_periodic_can.groups
        engine.stop_test(1)
        assert not engine.debug_periodic_can.groups
        count=d.send_can_message.call_count;time.sleep(.04);assert d.send_can_message.call_count==count
    finally:engine.stop_all()


@pytest.mark.parametrize('failed', [False, True])
def test_second_independent_debug_attempt_keeps_existing_sender(failed):
    from core.engine import ChannelWorker,TestEngine,TestStep,StepType
    engine=TestEngine();d=driver()
    engine.debug_periodic_can.start(start(),d,1)
    try:
        first=ChannelWorker(1,[])
        first.periodic_can=engine.debug_periodic_can
        first._keep_debug_periodic=True;first.is_running=True
        first.run_next_step()
        sender=engine.debug_periodic_can.groups['BMS']['thread']
        second=ChannelWorker(1,[TestStep('read',StepType.CUSTOM)])
        second.periodic_can=engine.debug_periodic_can
        second._keep_debug_periodic=True;second.is_running=True
        second.current_step_index=0
        if failed:
            second.on_step_complete(is_pass=False)
        else:
            second.current_step_index=1;second.run_next_step()
        assert engine.debug_periodic_can.groups['BMS']['thread'] is sender
        count=d.send_can_message.call_count
        time.sleep(.065)
        assert d.send_can_message.call_count>count
        engine.stop_test(1)
        assert not engine.debug_periodic_can.groups
    finally:engine.stop_all()


def driver():return SimpleNamespace(is_connected=True,fd_enabled=True,bus=object(),channel='PCAN_USBBUS1',last_error='',send_can_message=Mock(return_value=True))


def test_independent_periods_and_stop_prevents_further_sends():
    d=driver();service=PeriodicLists(lambda:False,lambda _:None)
    try:
        service.start(start(),d,1)
        time.sleep(.16)
        service.stop('BMS')
        ids=[call.args[1] for call in d.send_can_message.call_args_list]
        assert ids.count(0x123)>ids.count(0x456)>=2
        count=len(ids);time.sleep(.05);assert d.send_can_message.call_count==count
        with pytest.raises(ValueError):service.stop('BMS')
    finally:service.close()


def test_duplicate_start_cancel_and_cleanup():
    d=driver();cancel=threading.Event();service=PeriodicLists(cancel.is_set,lambda _:None)
    try:
        service.start(start(),d,1)
        with pytest.raises(ValueError):service.start(start(),d,1)
        cancel.set();assert service.close()==[]
        count=d.send_can_message.call_count;time.sleep(.04);assert d.send_can_message.call_count==count
        with pytest.raises(RuntimeError):service.start(start(),d,1)
    finally:service.close()


def test_background_failure_is_reported_and_not_silently_passed():
    d=driver();d.send_can_message.side_effect=[True,True,False]
    service=PeriodicLists(lambda:False,lambda _:None)
    try:
        service.start(start(),d,1)
        deadline=time.monotonic()+1
        while not service.groups['BMS']['error'] and time.monotonic()<deadline:time.sleep(.005)
        with pytest.raises(RuntimeError):service.check()
        assert service.close()
        assert not service.groups
    finally:service.close()


def test_invalid_frame_prevents_all_sends_and_fd_requires_fd_recipe():
    d=driver();service=PeriodicLists(lambda:False,lambda _:None)
    c=start();c['frames'][1]['data']='invalid'
    with pytest.raises(ValueError):service.start(c,d,1)
    d.send_can_message.assert_not_called()
    c=start();c['frames'][1]['type']=1;d.fd_enabled=False
    with pytest.raises(ValueError):service.start(c,d,1)
    d.send_can_message.assert_not_called()


def test_failed_list_can_restart_after_explicit_connection_recovery():
    d=driver();service=PeriodicLists(lambda:False,lambda _:None)
    try:
        service.start(start(),d,1)
        d.is_connected=False
        deadline=time.monotonic()+1
        while not service.groups['BMS']['stop'].is_set() and time.monotonic()<deadline:
            time.sleep(.005)
        assert service.groups['BMS']['stop'].is_set()
        assert service.active_names()==[]
        old=service.groups['BMS']['thread']
        count=d.send_can_message.call_count
        d.bus=object();d.is_connected=True
        validate_plan(plan(start()),debug=True,active_names=service.active_names())
        service.start(start(),d,1)
        assert not old.is_alive()
        assert service.active_names()==['BMS']
        assert d.send_can_message.call_count>=count+2
        with pytest.raises(ValueError):service.start(start(),d,1)
    finally:service.close()


def test_uds_ids_cannot_be_used_by_periodic_sender():
    d=driver();service=PeriodicLists(lambda:False,lambda _:None)
    try:
        service.start(start(),d,1)
        service.assert_uds_ids_available(d,0x7E0,0x7E8)
        with pytest.raises(ValueError):service.assert_uds_ids_available(d,0x123,0x7E8)
        with pytest.raises(ValueError):service.assert_uds_ids_available(d,0x7E0,0x456)
        service.assert_uds_ids_available(driver(),0x123,0x456)
    finally:service.close()


def test_changed_bus_never_reuses_old_list_on_new_adapter():
    d=driver();service=PeriodicLists(lambda:False,lambda _:None)
    try:
        service.start(start(),d,1);d.bus=object()
        deadline=time.monotonic()+1
        while not service.groups['BMS']['error'] and time.monotonic()<deadline:time.sleep(.005)
        with pytest.raises(RuntimeError):service.check()
        assert d.send_can_message.call_count==2
    finally:service.close()


def test_engine_start_stop_and_normal_finish_cleanup():
    from core.engine import ChannelWorker,SubStep,SubStepType
    d=driver();w=ChannelWorker(1,[],device_manager=SimpleNamespace(pcan=d))
    def execute(c):return w._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device='CAN 交互',params=encode(c))))
    try:
        assert not execute(stop())[0]
        assert execute(start())[0]
        assert execute(stop())[0]
        assert execute(start())[0]
        w.is_running=True;w.run_next_step()
        assert not w.periodic_can.groups
    finally:w.periodic_can.close()


def test_engine_binds_zlg_physical_channel_and_stop_cleans_up():
    from core.engine import ChannelWorker,SubStep,SubStepType
    d=driver();d.channel='ZLG_CAN1'
    mgr=SimpleNamespace(pcan=d,safe_shutdown_outputs=lambda *args,**kw:True)
    w=ChannelWorker(1,[],device_manager=mgr)
    try:
        ok,value=w._execute_sub_step_logic(SubStep(SubStepType.CAN_INTERACT,dict(device='CAN 交互',params=encode(start())+' / PCH:3')))
        assert ok,value
        assert all(call.kwargs['physical_channel']==3 for call in d.send_can_message.call_args_list)
        w.request_stop();w.stop()
        assert not w.periodic_can.groups
        count=d.send_can_message.call_count;time.sleep(.04);assert d.send_can_message.call_count==count
    finally:w.periodic_can.close()


def test_editor_start_stop_roundtrip():
    from ui.dialogs.step_dialog import StepDialog
    d=StepDialog()
    try:
        d.category_combo.setCurrentText('报文交互');d.device_combo.setCurrentText('CAN 交互')
        for c in (start(),stop()):
            d.action_combo.setCurrentText('启动周期报文列表' if c['operation']=='start' else '停止周期报文列表')
            assert d.param_stack.currentWidget() is d.can_periodic_form
            d.can_periodic_form.load(c);data=d.get_data()
            assert decode(data['params'])==c and data['type']=='CAN交互'
            restored=StepDialog(step_data=data)
            try:assert restored.can_periodic_form.config()==c
            finally:restored.close()
    finally:d.close()
