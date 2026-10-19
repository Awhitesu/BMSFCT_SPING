import threading
from types import SimpleNamespace
from unittest.mock import Mock, patch

import pytest

from core.simulator_control import control_simulator, read_simulator
from devices.ngi_83624 import NGI83624


def driver(states):
    d=NGI83624('unused')
    d.read_output_state=Mock(side_effect=lambda ch,logger=None:states[ch])
    d.read_source_settings=Mock(return_value={'range':'HIGH','current':.0005})
    for method in ('set_voltage','set_voltages','set_range','set_current_limit','output_controls','_verify_output_voltage'):
        setattr(d,method,Mock(return_value=True))
    return d


@pytest.mark.parametrize('action,value',[('voltage',4.2),('current',.0005)])
def test_live_parameter_requires_actual_voltage_confirmation(action,value):
    d=driver({1:True,2:False})
    assert control_simulator(d,action,[1,2],value,batch=True)
    args=d._verify_output_voltage.call_args
    assert args.args[0]==[1]
    assert args.kwargs['minimum_settle']==.5
    d.output_controls.assert_not_called()


def test_off_parameter_change_never_enables_or_requires_output_voltage():
    d=driver({1:False})
    assert control_simulator(d,'voltage',[1],4.2)
    d._verify_output_voltage.assert_not_called()
    d.output_controls.assert_not_called()


def test_range_cycles_only_previously_enabled_channels_and_preserves_limit():
    d=driver({1:True,2:False})
    events=Mock()
    events.attach_mock(d.output_controls,'output')
    events.attach_mock(d.set_range,'range')
    assert control_simulator(d,'range',[1,2],'LOW')
    assert events.mock_calls[0].args== (False,[1],None)
    assert events.mock_calls[-1].args== (True,[1],None)
    d.set_current_limit.assert_any_call(1,.0005,None)
    d.set_current_limit.assert_any_call(2,.0005,None)


def test_invalid_low_limit_does_not_modify_or_interrupt_output():
    d=driver({1:True})
    d.read_source_settings.return_value={'range':'HIGH','current':.01}
    with pytest.raises(ValueError,match='1 mA'):
        control_simulator(d,'range',[1],'LOW')
    d.output_controls.assert_not_called()
    d.set_range.assert_not_called()


def test_current_above_low_range_is_rejected_before_write():
    d=driver({1:True})
    d.read_source_settings.return_value={'range':'LOW','current':.0005}
    with pytest.raises(ValueError,match='1 mA'):
        control_simulator(d,'current',[1],.01)
    d.set_current_limit.assert_not_called()
    d.output_controls.assert_not_called()


def test_microamp_limit_not_silently_confirmed_as_zero():
    d=NGI83624('unused')
    d._safe_send=Mock(return_value=True)
    d._query=Mock(return_value='0')
    assert not d.set_current_limit(1,.0000001)


def test_live_confirmation_failure_closes_only_selected_active_channels():
    d=driver({1:True,2:False})
    d.last_error='actual voltage mismatch'
    d._verify_output_voltage.return_value=False
    with pytest.raises(RuntimeError,match='actual voltage mismatch'):
        control_simulator(d,'voltage',[1,2],4.2)
    d.output_controls.assert_called_once_with(False,[1],None)


def test_cleanup_exception_preserves_primary_control_error():
    d=driver({1:True})
    d.last_error='voltage mismatch'
    d._verify_output_voltage.return_value=False
    d.output_controls.side_effect=OSError('transport lost')
    with pytest.raises(RuntimeError,match='voltage mismatch.*transport lost'):
        control_simulator(d,'voltage',[1],4.2)


def test_cancel_during_range_change_never_reenables_output():
    d=driver({1:True})
    stopped=threading.Event()
    d.set_range.side_effect=lambda *a:stopped.set() or True
    with pytest.raises(RuntimeError,match='cancelled'):
        control_simulator(d,'range',[1],'LOW',cancelled=stopped.is_set)
    assert all(call.args[0] is False for call in d.output_controls.call_args_list)


def test_unknown_output_state_prevents_parameter_write():
    d=driver({1:None})
    with pytest.raises(RuntimeError,match='output states'):
        control_simulator(d,'voltage',[1],4.2)
    d.set_voltage.assert_not_called()


def test_control_log_records_request_and_confirmation():
    d=driver({1:False})
    logger=Mock()
    assert control_simulator(d,'voltage',[1],2.5,logger=logger)
    lines=[c.args[0] for c in logger.call_args_list]
    assert 'BEGIN' in lines[0] and 'value=2.5' in lines[0]
    assert 'CONFIRMED' in lines[-1] and 'elapsed=' in lines[-1]


def test_read_returns_latest_stable_out_of_spec_value_without_averaging():
    d=NGI83624('unused')
    d._query=Mock(side_effect=['4.2','3.56','3.561','3.561','3.561','3.561'])
    now=[0.]
    with patch('devices.ngi_83624.time.monotonic',side_effect=lambda:now[0]),patch(
        'devices.ngi_83624.time.sleep',side_effect=lambda t:now.__setitem__(0,now[0]+t)):
        value=read_simulator(d,'voltage',1)
    assert value==3.561
    assert now[0]>=.4
    assert d.last_measurement_confirmation['samples']>=5


def test_current_read_is_ma_and_does_not_switch_output():
    d=NGI83624('unused')
    d.read_source_settings=Mock(return_value={'range':'LOW','current':.0005})
    d._query=Mock(return_value='0.0008')
    d.output_controls=Mock()
    now=[0.]
    with patch('devices.ngi_83624.time.monotonic',side_effect=lambda:now[0]),patch(
        'devices.ngi_83624.time.sleep',side_effect=lambda t:now.__setitem__(0,now[0]+t)):
        assert d.read_stable(1,'current')==.0008
    d.output_controls.assert_not_called()
    assert d.last_measurement_confirmation['tolerance'] is None


def test_real_current_changes_are_returned_for_recipe_judgment():
    d=NGI83624('unused')
    d.read_source_settings=Mock(return_value={'range':'HIGH','current':.1})
    d._query=Mock(side_effect=['10','20','30','40','50'])
    now=[0.]
    with patch('devices.ngi_83624.time.monotonic',side_effect=lambda:now[0]),patch(
        'devices.ngi_83624.time.sleep',side_effect=lambda t:now.__setitem__(0,now[0]+t)):
        assert d.read_stable(1,'current')==40
    assert d.last_measurement_confirmation['value']==40


def test_persistently_changing_readback_times_out():
    d=NGI83624('unused')
    d._query=Mock(side_effect=['1','2']*30)
    now=[0.]
    with patch('devices.ngi_83624.time.monotonic',side_effect=lambda:now[0]),patch(
        'devices.ngi_83624.time.sleep',side_effect=lambda t:now.__setitem__(0,now[0]+t)):
        with pytest.raises(RuntimeError,match='did not stabilize'):
            d.read_stable(1,'voltage')


def test_read_cancellation_sends_no_query():
    d=NGI83624('unused')
    d._query=Mock()
    with pytest.raises(RuntimeError,match='cancelled'):
        d.read_stable(1,'voltage',cancelled=lambda:True)
    d._query.assert_not_called()


def test_current_read_cancelled_before_settings_query():
    d=NGI83624('unused')
    d.read_source_settings=Mock()
    with pytest.raises(RuntimeError,match='cancelled'):
        d.read_stable(1,'current',cancelled=lambda:True)
    d.read_source_settings.assert_not_called()


def test_nonfinite_read_is_failure():
    d=NGI83624('unused')
    d._query=Mock(return_value='nan')
    with pytest.raises(ValueError,match='Non-finite'):
        d.read_stable(1,'voltage')
    assert d.last_measurement_confirmation['ok'] is False


def test_snapshot_rejects_persistent_drift():
    d=NGI83624('unused')
    d.read_voltage_snapshot=Mock(side_effect=[[(i%2+1.,2.5,True)] for i in range(40)])
    now=[0.]
    with patch('devices.ngi_83624.time.monotonic',side_effect=lambda:now[0]),patch(
        'devices.ngi_83624.time.sleep',side_effect=lambda t:now.__setitem__(0,now[0]+t)):
        with pytest.raises(RuntimeError,match='did not stabilize'):
            d.read_stable_snapshot([1])


def test_ui_all_read_uses_stable_batch_interface():
    from ui.tabs.simulator_tab import ReadAllWorker
    d=NGI83624('unused')
    d.read_stable_snapshot=Mock(return_value=[(2.5,2.5,True)])
    d.read_voltage_snapshot=Mock()
    worker=ReadAllWorker([(25,d,1)])
    values=[]
    worker.progress.connect(lambda ch,value:values.append((ch,value)))
    worker.run()
    assert values==[(25,2.5)]
    assert d.read_stable_snapshot.call_args.args==([1],)
    d.read_voltage_snapshot.assert_not_called()


def test_ui_read_failure_preserves_diagnostic_reason():
    from ui.tabs.simulator_tab import ReadAllWorker
    d=NGI83624('unused')
    d.read_stable_snapshot=Mock(side_effect=RuntimeError('voltage kept drifting'))
    worker=ReadAllWorker([(1,d,1)])
    diagnostics=[]
    worker.diagnostic.connect(lambda ch,text:diagnostics.append(text))
    worker.run()
    assert 'voltage kept drifting' in diagnostics[0]


def test_typed_current_keeps_signed_sample_and_native_ma_unit():
    from core.device_steps import encode,execute
    d=NGI83624('unused')
    d.read_stable=Mock(return_value=-.0002)
    mgr=SimpleNamespace(is_virtual=False,_get_sim_and_ch=lambda ch:(d,ch))
    ok,value=execute(mgr,encode('simulator','current',{'channel':1}),{},lambda _:None)
    assert ok and value==-.0002 and value.unit=='mA'


def test_legacy_engine_simulator_current_uses_ma_and_propagates_cancellation():
    from core.engine import ChannelWorker
    from devices.manager import DeviceManager
    d=NGI83624('unused')
    d.read_stable=Mock(return_value=-.0002)
    mgr=DeviceManager.__new__(DeviceManager)
    mgr._get_sim_and_ch=lambda ch:(d,ch)
    worker=SimpleNamespace(channel_id=1,is_running=True)
    logs=[]
    ok,value=ChannelWorker._read_instrument_once(worker,mgr,
        {'device':'simulator','params':'CURRENT / CH:1'},logs.append)
    assert ok and value==-.0002 and value.unit=='mA'
    assert logs[-1].endswith('mA')
    cancel=d.read_stable.call_args.args[3]
    assert not cancel()
    worker.is_running=False
    assert cancel()
