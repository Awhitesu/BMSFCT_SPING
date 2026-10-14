import unittest
from unittest.mock import Mock
from devices.tektronix_tbs2102b import TBS2102B


class ScopeTests(unittest.TestCase):
    def test_pulse_trigger_commands(self):
        scope=TBS2102B()
        scope.apply=Mock(return_value='OK')
        scope.command=Mock(return_value='1.48')
        scope.trigger('WIDTH','CH2','NORMAL',1.5,'RISE','DC',20e-9,'POSITIVE','LESSTHAN',1e-5,.8,2)
        commands=scope.apply.call_args.args[0]
        self.assertIn('TRIG:A:TYPE PULSE',commands)
        self.assertIn('TRIG:A:PULSE:CLASS WIDTH',commands)
        self.assertIn('TRIG:A:PULSE:SOURCE CH2',commands)
        self.assertIn('TRIG:A:LEVEL:CH2 1.5',commands)

    def test_trigger_returns_requested_and_quantized_actual(self):
        scope=TBS2102B();scope.apply=Mock(return_value='OK')
        scope.command=Mock(side_effect=['1.6400','AUTO;EDGE'])
        report=scope.trigger('EDGE','CH1','AUTO',1.65,'RISE','DC',20e-9,
                             'POSITIVE','LESSTHAN',1e-5,.8,2)
        self.assertEqual(report['requested'],{'level':1.65})
        self.assertEqual(report['actual'],{'level':1.64})
        scope.command.assert_any_call('TRIG:A:LEVEL:CH1?')

    def test_runt_reads_both_actual_thresholds(self):
        scope=TBS2102B();scope.command=Mock(side_effect=['0.8','1.96','PULSE;RUNT'])
        report=scope.read_trigger_settings('RUNT','CH2',{'lower':.8,'upper':2})
        self.assertEqual(report['actual'],{'lower':.8,'upper':1.96})

    def test_invalid_readback_is_not_reported_as_success(self):
        scope=TBS2102B();scope.command=Mock(return_value='nan')
        with self.assertRaises(ValueError):scope.read_trigger_settings('EDGE','CH1')

    def test_runt_invalid_threshold_blocks_apply(self):
        scope=TBS2102B()
        scope.apply=Mock()
        with self.assertRaises(ValueError):
            scope.trigger('RUNT','CH1','NORMAL',1,'RISE','DC',20e-9,'POSITIVE','OCCURS',1e-5,2,1)
        scope.apply.assert_not_called()

    def waveform_scope(self,running=True):
        scope=TBS2102B();scope.is_connected=True
        replies={'SELECT:CH1?':'1','SELECT:CH2?':'0',
                 'ACQ:STATE?':'1' if running else '0','*OPC?':'1',
                 'DATA:SOURCE?':'CH2','DATA:ENCDG?':'RIBINARY',
                 'DATA:START?':'2','DATA:STOP?':'2',
                 'WFMOUTPRE:RECORDLENGTH?':'3','WFMOUTPRE:NR_PT?':'3',
                 'WFMOUTPRE:XINCR?':'.1','WFMOUTPRE:XZERO?':'-.2',
                 'WFMOUTPRE:YMULT?':'.01','WFMOUTPRE:YOFF?':'1',
                 'WFMOUTPRE:YZERO?':'.5','WFMOUTPRE:PT_OFF?':'1'}
        scope.command=Mock(side_effect=lambda command:replies.get(command,'sent'))
        scope.resource=Mock();scope.resource.query_ascii_values.return_value=[1,2,3]
        return scope

    def test_waveform_pauses_restores_and_scales(self):
        scope=self.waveform_scope()
        points=scope.waveform('CH1')
        self.assertEqual(len(points),3)
        self.assertAlmostEqual(points[0][0],-.3)
        self.assertAlmostEqual(points[2][1],.52)
        scope.command.assert_any_call('ACQ:STATE OFF')
        scope.command.assert_any_call('DATA:STOP 3')
        scope.command.assert_any_call('DATA:SOURCE CH2')
        scope.command.assert_any_call('DATA:START 2')
        self.assertEqual(scope.command.call_args.args[0],'ACQ:STATE ON')

    def test_waveform_failure_resumes_acquisition(self):
        scope=self.waveform_scope()
        scope.resource.query_ascii_values.return_value=[1]
        with self.assertRaises(ValueError):scope.waveform('CH1')
        self.assertEqual(scope.command.call_args.args[0],'ACQ:STATE ON')

    def test_stopped_waveform_stays_stopped(self):
        scope=self.waveform_scope(False)
        scope.waveform('CH1')
        self.assertNotIn(('ACQ:STATE ON',),[c.args for c in scope.command.call_args_list])

    def test_hidden_channel_does_not_pause_or_query_curve(self):
        scope=self.waveform_scope()
        with self.assertRaises(ValueError):scope.waveform('CH2')
        scope.command.assert_called_once_with('SELECT:CH2?')
        scope.resource.query_ascii_values.assert_not_called()
