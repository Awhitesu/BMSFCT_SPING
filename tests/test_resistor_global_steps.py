import os
os.environ.setdefault('QT_QPA_PLATFORM','offscreen')
from contextlib import nullcontext
from types import SimpleNamespace
import pytest
from PySide6.QtWidgets import QApplication
from core.device_steps import encode,decode,execute,action_spec
from devices.resistor_bank import select_channels,channel_routes
from test_resistor_bank import board
from ui.widgets.device_step_form import DeviceStepForm

app=QApplication.instance() or QApplication([])


@pytest.mark.parametrize('text,expected',[
    ('7',[7]),('1,7,13',[1,7,13]),('7-12',list(range(7,13))),
    ('所有',list(range(1,37))),('所有奇数',list(range(1,37,2))),('所有偶数',list(range(2,37,2))),
    ('1，7；13',[1,7,13])])
def test_channel_notation(text,expected):assert select_channels(text)==expected


@pytest.mark.parametrize('text',['','0','37','12-7','1,1','1-3,3','foo','1,,2'])
def test_invalid_notation(text):
    with pytest.raises(ValueError):select_channels(text)


def test_steps_route_by_hardware_sequence_and_read_actual_setpoint():
    # Physical dictionary order and key are deliberately different from sequence.
    cards={'physicalA':board(2),'physicalB':board(1),'last':board(6)}
    manager=SimpleNamespace(resistor_routes=lambda:channel_routes(cards),device_step_lock=lambda key:nullcontext())
    ok,value=execute(manager,encode('resistor','resistance_batch',dict(channels='7,18',resistance=10000)),{},lambda _:None)
    assert ok and value=='PASS'
    cards['physicalA'].set_resistance.assert_called_once_with(10000,1)
    cards['last'].set_resistance.assert_called_once_with(10000,6)
    cards['physicalB'].set_resistance.assert_not_called()
    variables={}
    ok,value=execute(manager,encode('resistor','read_resistance_batch',dict(channels='7',variable='R')),variables,lambda _:None)
    assert ok and float(value)==2001 and variables['R']==value
    _,value=execute(manager,encode('resistor','read_resistance_batch',dict(channels='7,18')),{},lambda _:None)
    assert 'CH7=2001' in value and 'CH18=6006' in value
    for card in cards.values():card.set_resistance.reset_mock()
    with pytest.raises(ValueError):execute(manager,encode('resistor','resistance_batch',dict(channels='1,19',resistance=1000)),{},lambda _:None)
    for card in cards.values():card.set_resistance.assert_not_called()
    assert select_channels('所有奇数',manager.resistor_routes())==list(range(1,19,2))


def test_editor_saves_only_global_selection_and_resistance():
    form=DeviceStepForm()
    try:
        spec=action_spec('resistor','设置阻值（全局通道）')
        form.configure('resistor',spec)
        assert set(form.controls)=={'channels','resistance'}
        for selection in ('所有奇数','7-12','1,7,13'):
            form.controls['channels'].setCurrentText(selection)
            values=form.values()
            assert decode(encode('resistor',spec.key,values))[2]['channels']==selection
    finally:form.close()
