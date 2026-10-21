"""Burst receive must retain every DBC multiplex page without using hardware."""
import hashlib
import time
from types import SimpleNamespace

import pytest

from core.dbc_signals import DBCMonitor, database
from devices.pcan_fd_driver import PCANFDDriver


def asset_from_text(text):
    return dict(name='multiplex.dbc', text=text,
                sha256=hashlib.sha256(text.encode('utf-8')).hexdigest())


@pytest.fixture
def cyclic_asset():
    parts=['VERSION ""', 'NS_ :', 'BS_:', 'BU_: BMS']
    for ident, name, prefix, factor, offset, unit in [
        (0x507, 'CAN_CELL_NTC', 'CELL_NTC', .01, -50, 'degC'),
        (0x511, 'CAN_CELL_VOLTAGE', 'CELL_VOL', .0001, 0, 'V'),
    ]:
        parts += [f'BO_ {ident} {name}: 8 BMS',
                  ' SG_ INDEX0 M : 7|8@0+ (1,0) [0|31] "" BMS',
                  ' SG_ INDEX1 : 39|8@0+ (1,0) [0|31] "" BMS']
        for index in range(32):
            for channel, start in [(index*2+1, 23), (index*2+2, 55)]:
                parts.append(f' SG_ {prefix}_{channel} m{index} : {start}|16@0+ '
                             f'({factor},{offset}) [-50|65535] "{unit}" BMS')
    return asset_from_text('\n'.join(parts))


@pytest.fixture
def receiver():
    driver=PCANFDDriver(channel='ZLG_CAN1', fd_enabled=False)
    driver.bus=object()
    driver.is_connected=True
    monitor=DBCMonitor()
    yield monitor, driver
    monitor.stop()
    driver.is_connected=False
    driver.bus=None


def receive(driver, ident, payload, channel=2):
    driver._publish(dict(direction='RX', interface=f'ZLG_CAN{channel}',
        physical_channel=channel, can_id=ident, is_extended_id=False, data=payload))


def until(predicate):
    deadline=time.perf_counter()+2
    while not predicate():
        assert time.perf_counter()<deadline
        time.sleep(.002)


@pytest.mark.parametrize('name,prefix,value', [
    ('CAN_CELL_NTC', 'CELL_NTC', 25),
    ('CAN_CELL_VOLTAGE', 'CELL_VOL', 3.8),
])
def test_channel_2_burst_keeps_all_32_pages_and_other_monitor_frames(cyclic_asset, receiver, name, prefix, value):
    monitor,driver=receiver
    monitor.configure(cyclic_asset, driver)
    message=database(cyclic_asset).get_message_by_name(name)
    with monitor._condition:
        for index in range(32):
            payload=message.encode(dict(INDEX0=index, INDEX1=index,
                **{f'{prefix}_{index*2+1}':value, f'{prefix}_{index*2+2}':value}))
            receive(driver, message.frame_id, payload)
        assert len(monitor._pending)==32
    until(lambda:len(monitor.snapshot())==66)
    snapshot=monitor.snapshot()
    assert driver.msg_queue.qsize()==32
    assert len(driver._rx_history)==32
    for channel in range(1,65):
        assert snapshot[('ZLG_CAN2', message.frame_id, False, f'{prefix}_{channel}')]['value']==pytest.approx(value)
        assert ('ZLG_CAN1', message.frame_id, False, f'{prefix}_{channel}') not in snapshot
    assert not monitor.last_error


def test_reported_raw_frame_updates_59_and_60_on_channel_2(cyclic_asset, receiver):
    monitor,driver=receiver
    monitor.configure(cyclic_asset, driver)
    receive(driver, 0x507, bytes.fromhex('1D 4C 1D 4C 1D 4C 1D 4C'))
    until(lambda:len(monitor.snapshot())==4)
    snapshot=monitor.snapshot()
    for name in ('CELL_NTC_59', 'CELL_NTC_60'):
        assert snapshot[('ZLG_CAN2', 0x507, False, name)]['value']==25
    assert ('ZLG_CAN2', 0x507, False, 'CELL_NTC_57') not in snapshot
    # A received single page must never invent values for other pages.
    assert not monitor.last_error


@pytest.mark.parametrize('start,order', [(3,'0'), (3,'1')])
def test_cross_byte_selectors_keep_latest_per_page_and_latest_common_values(receiver, start, order):
    text=f'''VERSION ""
NS_ :
BS_:
BU_: BMS
BO_ 600 CrossByte: 8 BMS
 SG_ Index M : {start}|12@{order}+ (1,0) [0|4095] "" BMS
 SG_ A m0 : 16|8@1+ (1,0) [0|255] "" BMS
 SG_ B m2748 : 16|8@1+ (1,0) [0|255] "" BMS
'''
    asset=asset_from_text(text)
    monitor,driver=receiver
    monitor.configure(asset, driver)
    message=database(asset).messages[0]
    with monitor._condition:
        receive(driver, 600, message.encode(dict(Index=0,A=10)))
        receive(driver, 600, message.encode(dict(Index=2748,B=20)))
        receive(driver, 600, message.encode(dict(Index=0,A=30)))
        assert len(monitor._pending)==2
    until(lambda:len(monitor.snapshot())==3)
    snapshot=monitor.snapshot()
    assert snapshot[('ZLG_CAN2',600,False,'A')]['value']==30
    assert snapshot[('ZLG_CAN2',600,False,'B')]['value']==20
    assert snapshot[('ZLG_CAN2',600,False,'Index')]['value']==0
    assert snapshot[('ZLG_CAN2',600,False,'Index')]['received']==snapshot[('ZLG_CAN2',600,False,'A')]['received']
    assert snapshot[('ZLG_CAN2',600,False,'B')]['received']<snapshot[('ZLG_CAN2',600,False,'A')]['received']


def test_undefined_indexes_share_one_bounded_slot(receiver):
    asset=asset_from_text('''VERSION ""
NS_ :
BS_:
BU_: BMS
BO_ 600 Data: 8 BMS
 SG_ Index M : 0|16@1+ (1,0) [0|65535] "" BMS
 SG_ A m0 : 16|8@1+ (1,0) [0|255] "" BMS
 SG_ B m1 : 16|8@1+ (1,0) [0|255] "" BMS
''')
    monitor,driver=receiver
    monitor.configure(asset, driver)
    with monitor._condition:
        for index in range(2,2002):
            receive(driver, 600, index.to_bytes(2,'little')+bytes(6))
        assert len(monitor._pending)==1
        receive(driver, 600, b'\x00\x00\x0a'+bytes(5))
        receive(driver, 600, b'\x01\x00\x14'+bytes(5))
        assert len(monitor._pending)==3
    until(lambda:len(monitor.snapshot())==3)
    assert not monitor.last_error


def test_nested_selectors_ignore_bits_of_inactive_selector(receiver):
    from cantools.database import Database
    from cantools.database.can import Message, Signal
    message=Message(600,'Nested',8,[
        Signal('Root',0,8,is_multiplexer=True),
        Signal('Child',8,8,is_multiplexer=True,multiplexer_signal='Root',multiplexer_ids=[0]),
        Signal('A',16,8,multiplexer_signal='Child',multiplexer_ids=[1]),
        Signal('B',16,8,multiplexer_signal='Child',multiplexer_ids=[2]),
        Signal('Other',8,8,multiplexer_signal='Root',multiplexer_ids=[1]),
    ])
    asset=asset_from_text(Database([message]).as_dbc_string())
    monitor,driver=receiver
    monitor.configure(asset, driver)
    with monitor._condition:
        receive(driver,600,message.encode(dict(Root=0,Child=1,A=10)))
        receive(driver,600,message.encode(dict(Root=0,Child=2,B=20)))
        for value in range(100):
            receive(driver,600,message.encode(dict(Root=1,Other=value)))
        assert len(monitor._pending)==3
    until(lambda:len(monitor.snapshot())==5)
    snapshot=monitor.snapshot()
    assert snapshot[('ZLG_CAN2',600,False,'A')]['value']==10
    assert snapshot[('ZLG_CAN2',600,False,'B')]['value']==20
    assert snapshot[('ZLG_CAN2',600,False,'Other')]['value']==99


def test_debug_ui_displays_channel_2_raw_frame(cyclic_asset, receiver):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QApplication
    from ui.widgets.dbc_forms import DBCMonitorTab
    app=QApplication.instance() or QApplication([])
    monitor,driver=receiver
    monitor.configure(cyclic_asset,driver)
    page=DBCMonitorTab(SimpleNamespace(dbc_monitor=monitor,pcan=driver))
    page.setAttribute(Qt.WA_DontShowOnScreen)
    try:
        receive(driver,0x507,bytes.fromhex('1D 4C 1D 4C 1D 4C 1D 4C'))
        until(lambda:len(monitor.snapshot())==4)
        page.show()
        app.processEvents()
        page.channel.setCurrentText('ZLG_CAN2')
        page.refresh()
        row=next(i for i,r in enumerate(page._rows) if r['signal']=='CELL_NTC_59')
        assert float(page.table.item(row,2).text())==25
        assert page.table.item(row,6).text()=='有效'
    finally:
        page.close()
