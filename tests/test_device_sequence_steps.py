import json
import threading
import unittest
import tempfile
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
from core.device_steps import ACTIONS,DEVICES,encode,decode,execute,validate
from core.measurement_units import MeasurementValue
from devices.manager import DeviceManager


def driver():
    d=Mock()
    d._lock=threading.RLock();d.is_connected=True;d.last_error=''
    d.connect.return_value=True
    d.query.return_value=d.command.return_value=d.get_idn.return_value='IDENTITY'
    d.check_success.return_value=True
    return d


class DeviceSequenceTests(unittest.TestCase):
    def setUp(self):
        self.manager=SimpleNamespace(is_enabled=lambda _:True,
            device_step_lock=lambda _:threading.RLock())
        self.variables={};self.logs=[]

    def run_step(self,key,action,**values):
        return execute(self.manager,encode(key,action,values),self.variables,self.logs.append)

    def test_schema_roundtrip_every_action(self):
        for key,specs in ACTIONS.items():
            for spec in specs:
                with self.subTest(device=key,action=spec.key):
                    decoded=decode(encode(key,spec.key,{}))
                    self.assertEqual(decoded[:2],(key,spec))
                    self.assertEqual(set(decoded[2]),{f.key for f in spec.fields})

    def test_it6333_channel_and_output(self):
        d=self.manager.it6333b=driver()
        d.set_voltage.return_value=d.set_current.return_value=d.output_control.return_value=True
        self.assertEqual(self.run_step('it6333b','configure',channel=3,voltage=4.5,current=.2,state='ON'),(True,'PASS'))
        d.set_voltage.assert_called_once_with(3,4.5)
        d.output_control.assert_called_once_with(3,True)
        d.read_channel.return_value=(4.49,.01,True)
        ok,result=self.run_step('it6333b','voltage',channel=3,variable='DUT_V')
        self.assertTrue(ok);self.assertEqual(result.unit,'V');self.assertEqual(self.variables['DUT_V'],4.49)
        with self.assertRaises(ValueError):encode('it6333b','configure',{'channel':3,'voltage':6})

    def test_driver_failure_is_not_pass(self):
        d=self.manager.it6333b=driver();d.output_control.return_value=False
        with self.assertRaises(RuntimeError):self.run_step('it6333b','on')

    def test_dmm_measure_preserves_signed_value_and_unit(self):
        d=self.manager.dmm34461=driver();d.measure.return_value=(-1.25,'V')
        ok,result=self.run_step('dmm34461','measure',function='直流电压',range='AUTO',variable='V')
        self.assertTrue(ok);self.assertEqual(result,-1.25);self.assertEqual(result.unit,'V')

    def test_generator_selects_channel_and_load(self):
        d=self.manager.dg2052=driver();d.configure.return_value={'频率(Hz)':'1000'}
        self.assertEqual(self.run_step('dg2052','configure',channel=2,state='ON'),(True,'PASS'))
        self.assertEqual(d.configure.call_args.args[0],2)
        d.output_control.assert_called_once_with(2,True)
        d.query.return_value='1000'
        self.assertEqual(self.run_step('dg2052','frequency',channel=2)[1],1000)

    def test_scope_trigger_quantization_and_tolerance(self):
        d=self.manager.tbs2102b=driver()
        d.trigger.return_value={'requested':{'level':1.65},'actual':{'level':1.64}}
        self.assertEqual(self.run_step('tbs2102b','trigger'),(True,'PASS'))
        self.assertEqual(self.variables['SCOPE_CH1_LEVEL'],1.64)
        with self.assertRaises(ValueError):self.run_step('tbs2102b','trigger',tolerance=.001)

    def test_scope_numeric_measurement_and_invalid_value(self):
        d=self.manager.tbs2102b=driver();d.command.side_effect=['3.3','"V"']
        result=self.run_step('tbs2102b','measure',measurement='PK2PK')[1]
        self.assertEqual(result,3.3);self.assertEqual(result.unit,'V')
        d.command.side_effect=['9.9e37','"V"']
        with self.assertRaises(ValueError):self.run_step('tbs2102b','measure')

    def test_scope_single_capture_timeout_stops(self):
        d=self.manager.tbs2102b=driver();d.command.return_value='1'
        with patch('core.device_steps.time.monotonic',side_effect=[0,11]):
            with self.assertRaises(TimeoutError):self.run_step('tbs2102b','single')
        d.command.assert_called_with('ACQ:STATE OFF')

    def test_each_board_kind_and_local_channel(self):
        for kind in ('insulation','resistor','relay','hv','hsd','dmm'):
            with self.subTest(kind=kind):
                d=driver();d.setting={'kind':kind}
                self.manager.get_rn_board=Mock(return_value=d)
                action='resistance' if kind in ('insulation','resistor') else 'on'
                self.assertEqual(self.run_step(kind,action),(True,'PASS'))
                self.manager.get_rn_board.assert_called_once_with(kind+'1')
        d=driver();d.setting={'kind':'resistor'};d.read_resistance.return_value=1500000
        self.manager.get_rn_board=Mock(return_value=d)
        result=self.run_step('resistor','read_resistance',board=6,channel=6)[1]
        d.read_resistance.assert_called_once_with(6)
        self.manager.get_rn_board.assert_called_once_with('resistor6')
        self.assertEqual(result.unit,'Ω')

    def test_insulation_zero_is_off_and_on_requires_resistance(self):
        d=driver();d.setting={'kind':'insulation'};self.manager.get_rn_board=Mock(return_value=d)
        self.assertEqual(self.run_step('insulation','off',board=2),(True,'PASS'))
        d.set_resistance_output.assert_called_once_with(False,0)
        with self.assertRaises(ValueError):encode('insulation','on',{'resistance':0})

    def test_simulator_global_channel_routes_to_physical_device(self):
        d=driver();d.output_control.return_value=True;d.measure_voltage.return_value=2.8
        self.manager._get_sim_and_ch=Mock(return_value=(d,6))
        self.assertEqual(self.run_step('simulator','on',channel=78),(True,'PASS'))
        d.output_control.assert_called_once_with(6,True,self.logs.append)
        self.assertEqual(self.run_step('simulator','voltage',channel=78)[1],2.8)

    def test_legacy_devices_new_actions(self):
        for name in ('dut_power','hv_source','ca550','easy320'):
            setattr(self.manager,name,driver())
            if name=='easy320':self.manager.easy320.read_relays.return_value=[False]*32
            self.assertEqual(self.run_step(name,'connect'),(True,'PASS'))
        self.manager.easy320.read_relays.return_value=[False,True]+[False]*30
        self.assertEqual(self.run_step('easy320','state',channel=2)[1],1)

    def test_disabled_instrument_blocks_before_connect(self):
        self.manager.is_enabled=lambda _:False
        self.manager.dg2052=driver()
        with self.assertRaises(ValueError):self.run_step('dg2052','on')
        self.manager.dg2052.connect.assert_not_called()

    def test_connection_check_probes_even_when_marked_connected(self):
        d=self.manager.dg2052=driver();d.query.return_value=''
        with self.assertRaises(ConnectionError):self.run_step('dg2052','connect')
        d.query.assert_called_once_with('*IDN?')

    def test_dry_run_every_action_never_accesses_real_drivers(self):
        from devices.mock_manager import MockDeviceManager
        manager=MockDeviceManager()
        with patch('devices.rn_board.RNBoard.connect') as connect:
            for key,specs in ACTIONS.items():
                for spec in specs:
                    with self.subTest(device=key,action=spec.key):
                        ok,result=execute(manager,encode(key,spec.key,{}),{},self.logs.append)
                        self.assertTrue(ok)
                        self.assertEqual(isinstance(result,MeasurementValue),spec.read)
            connect.assert_not_called()
        self.assertTrue(all('[仿真' in line for line in self.logs))

    def test_scope_waveform_csv_and_variables(self):
        d=self.manager.tbs2102b=driver();d.waveform.return_value=[(0,1),(.1,2)]
        with tempfile.TemporaryDirectory() as root:
            with patch('core.device_steps.__file__',str(Path(root)/'core/device_steps.py')):
                self.assertEqual(self.run_step('tbs2102b','waveform'),(True,'PASS'))
                path=Path(self.variables['SCOPE_CH1_WAVEFORM_FILE'])
                self.assertTrue(path.is_file())
                self.assertEqual(self.variables['SCOPE_CH1_WAVEFORM_POINTS'],2)
                self.assertIn('time_s,voltage_V',path.read_text(encoding='utf-8-sig'))

    def test_scope_single_capture_cancel_stops(self):
        d=self.manager.tbs2102b=driver()
        with self.assertRaises(RuntimeError):
            execute(self.manager,encode('tbs2102b','single',{}),{},self.logs.append,cancelled=lambda:True)
        d.command.assert_called_once_with('ACQ:STATE OFF')

    def test_invalid_schema_rejects_nan_and_bad_channel(self):
        for values in ({'channel':4},{'voltage':float('nan')},{'surprise':1}):
            with self.assertRaises(ValueError):encode('it6333b','configure',values)
        with self.assertRaises(ValueError):encode('resistor','resistance',{'resistance':11})

    def test_board_manager_reuses_driver_and_reloads_config(self):
        cfg={'rn_boards':{'resistor6':{'enabled':True,'ip':'192.168.1.99'}}}
        db=Mock();db.load_sys_config.side_effect=lambda:cfg
        manager=DeviceManager(db)
        one=manager.get_rn_board('resistor6')
        self.assertIs(manager.get_rn_board('resistor6'),one)
        cfg['rn_boards']['resistor6']['ip']='192.168.1.98'
        two=manager.get_rn_board('resistor6');self.assertIsNot(one,two)
        self.assertEqual(two.ip,'192.168.1.98')
        with self.assertRaises(ValueError):manager.get_rn_board('resistor1')


if __name__=='__main__':unittest.main()
