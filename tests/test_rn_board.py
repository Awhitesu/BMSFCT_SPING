import unittest
from unittest.mock import Mock
import struct
from devices.rn_board import board_settings,RNBoard


class BoardTests(unittest.TestCase):
    def test_inventory(self):
        settings=board_settings({})
        self.assertEqual(len(settings),14)
        self.assertEqual(sum(s['kind']=='resistor' for s in settings),6)
        self.assertEqual([s['limit'] for s in settings if s['kind']=='resistor'],[1500000]*6)
        self.assertEqual([s['channels'] for s in settings if s['kind']=='resistor'],[6]*6)

    def test_resistance_atomic_write(self):
        s=next(s for s in board_settings({}) if s['kind']=='insulation')
        s['limit']=5000000
        b=RNBoard(s); b.request=Mock(return_value=struct.pack('>HH',0,2)); b.read_resistance=Mock(return_value=1500000)
        self.assertEqual(b.set_resistance(1500000),1500000)
        b.request.assert_called_once_with(16,struct.pack('>HHBHH',0,2,4,1500000>>16,1500000&65535))
        with self.assertRaises(ValueError): b.set_resistance(5001000)
        with self.assertRaises(ValueError): b.set_resistance(11)
        with self.assertRaises(ValueError): b.set_resistance(1234)

    def test_relay_bit_order(self):
        s=next(s for s in board_settings({}) if s['kind']=='dmm')
        b=RNBoard(s); b.request=Mock(return_value=b'\x03\x01\x80\x02')
        states=b.read_coils()
        self.assertEqual([i+1 for i,v in enumerate(states) if v],[1,16,18])

    def test_unconfirmed_insulation_blocks_write(self):
        s=board_settings({})[0]; s['limit']=0
        b=RNBoard(s)
        with self.assertRaises(ValueError): b.set_resistance(1000)

    def test_six_channel_address_and_atomic_write(self):
        b=RNBoard(next(s for s in board_settings({}) if s['kind']=='resistor'))
        for channel in range(1,7):
            head=struct.pack('>HH',(channel-1)*2,2)
            b.request=Mock(return_value=head); b.read_resistance=Mock(return_value=1500000)
            self.assertEqual(b.set_resistance(1500000,channel),1500000)
            b.request.assert_called_once_with(16,head+b'\x04'+struct.pack('>HH',22,58208))
            b.read_resistance.assert_called_once_with(channel)
        b.request.reset_mock()
        for value,channel in [(1500010,1),(11,1),(1000,0),(1000,7)]:
            with self.assertRaises(ValueError): b.set_resistance(value,channel)
        b.request.assert_not_called()

    def test_read_all_six_channels(self):
        b=RNBoard(next(s for s in board_settings({}) if s['kind']=='resistor'))
        values=[0,10,1000,100000,500000,1500000]
        b.read_registers=Mock(return_value=[word for v in values for word in (v>>16,v&65535)])
        self.assertEqual(b.read_resistances(),{'resistances':values})
        b.read_registers.assert_called_once_with(0,12)

    def test_documented_relay_end_channels_and_bulk_masks(self):
        for kind,count,mask in [('relay',12,b'\xff\x0f'),('hv',10,b'\xff\x03'),('hsd',15,b'\xff\x7f'),('dmm',18,b'\xff\xff\x03')]:
            b=RNBoard(next(s for s in board_settings({}) if s['kind']==kind))
            payload=struct.pack('>HH',count-1,0xff00)
            b.request=Mock(return_value=payload); b.read_coils=Mock(return_value=[True]*count)
            b.set_relay(count,True); b.request.assert_called_once_with(5,payload)
            head=struct.pack('>HH',0,count); b.request=Mock(return_value=head)
            b.all_relays(True); b.request.assert_called_once_with(15,head+bytes([len(mask)])+mask)

    def test_insulation_output_off(self):
        s=board_settings({})[0]; s['limit']=5000000
        b=RNBoard(s); b.request=Mock(return_value=struct.pack('>HH',0,2)); b.read_resistance=Mock(return_value=0); b.read_registers=Mock(return_value=[0])
        self.assertEqual(b.set_resistance_output(False,2000),{'resistance':0,'output':False})
        b.request.assert_called_once_with(16,struct.pack('>HHBHH',0,2,4,0,0))
        b.read_registers.assert_called_once_with(0x104,1)

    def test_insulation_output_on_and_status_failure(self):
        b=RNBoard(board_settings({})[0]); b.set_resistance=Mock(); b.read_registers=Mock(return_value=[1])
        self.assertEqual(b.set_resistance_output(True,5000000),{'resistance':5000000,'output':True})
        b.set_resistance.assert_called_once_with(5000000)
        b.read_registers.return_value=[0]
        with self.assertRaises(ValueError): b.set_resistance_output(True,1000)

    def test_confirmed_5m_defaults(self):
        cfg={'rn_boards':{'insulation1':{'limit':1500000},'insulation2':{'limit':0}}}
        self.assertEqual([s['limit'] for s in board_settings(cfg) if s['kind']=='insulation'],[5000000,5000000])
