import pytest
from PySide6.QtWidgets import QApplication
from core.can_data_parser import encode_parser, decode_parser, parse_payload
from core.engine import ChannelWorker
from ui.widgets.can_parser_form import CanParserForm
from ui.dialogs.step_dialog import StepDialog

app = QApplication.instance() or QApplication([])


def config(**changes):
    c = dict(length=8, start=1, count=2, bit=0, mode='bytes', type='unsigned',
             endian='big', scale=.01, offset=0, unit='V')
    c.update(changes)
    return c


def test_preview_runtime_share_parser_and_log_raw_rule_result():
    data = bytes.fromhex('01 02 34 00 00 00 00 00')
    c = config()
    assert parse_payload(data, c) == (564, 5.64)
    logs = []
    class Worker:
        _parse_key_values = ChannelWorker._parse_key_values
    worker = Worker()
    value = ChannelWorker._parse_can_result(worker, {'params': 'PARSE:' + encode_parser(c)}, data, logs.append)
    assert value == 5.64 and '564' in logs[0] and '5.64' in logs[0]
    with pytest.raises(ValueError, match='实际'):
        parse_payload(data[:-1], c)


@pytest.mark.parametrize('c,data,expected', [
    (config(length=2, start=0, count=2, endian='little', scale=1), b'\x02\x01', 258),
    (config(length=1, start=0, count=1, type='signed', scale=1), b'\xff', -1),
    (config(length=2, start=0, bit=7, count=3, mode='bits', scale=1), b'\x80\x03', 7),
    (config(length=1, start=0, bit=2, count=3, mode='bits', type='signed', scale=1), b'\x1c', -1),
    (config(length=4, start=0, count=4, type='float', scale=1), bytes.fromhex('3f800000'), 1),
    (config(length=2, start=0, count=2, type='ascii', scale=1), b'AB', 'AB'),
])
def test_data_types(c, data, expected):
    assert parse_payload(data, c)[1] == expected


@pytest.mark.parametrize('changes', [dict(start=7, count=2), dict(scale=float('nan')),
    dict(type='float', count=2), dict(mode='bits', type='ascii'), dict(count=0)])
def test_invalid_rules_cannot_save(changes):
    with pytest.raises(ValueError):
        encode_parser(config(**changes))


def test_manual_table_preview_and_roundtrip():
    form = CanParserForm()
    form.enabled.setChecked(True)
    form.sample.setText('01 02 34 00 00 00 00 00')
    form.start.setValue(1); form.count.setValue(2); form.scale.setText('0.01')
    assert '5.64' in form.preview.text()
    assert form.bytes_table.item(1, 0).background().color().name() == '#245d85'
    form.bytes_table.clearSelection()
    form.bytes_table.item(3, 0).setSelected(True)
    form.bytes_table.commit_selection()
    assert form.start.value() == 3 and form.count.value() == 1
    form.bits_table.item(0, 7).setSelected(True)
    form.bits_table.commit_selection()
    assert form.mode.currentIndex() == 1 and form.bit.value() == 0
    value = form.encoded()
    restored = CanParserForm(); restored.load(value)
    assert restored.config() == decode_parser(value)
    form.close(); restored.close()


def test_step_save_load_parser_and_send_excludes_it():
    dialog = StepDialog()
    dialog.category_combo.setCurrentText('报文交互')
    dialog.device_combo.setCurrentText('CAN 交互')
    dialog.action_combo.setCurrentText('交互/问答')
    dialog.can_parser.load(encode_parser(config()))
    saved = dialog.get_data()
    assert 'PARSE:' in saved['params']
    restored = StepDialog(step_data=saved)
    assert restored.can_parser.config() == config()
    dialog.action_combo.setCurrentText('发送指令')
    assert 'PARSE:' not in dialog.get_data()['params']
    dialog.close(); restored.close()
