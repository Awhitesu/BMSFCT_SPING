import threading
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from devices.resistor_bank import channel_routes,operate_channels


def board(number):
    result=SimpleNamespace(setting={'kind':'resistor','number':number,'enabled':True},
        is_connected=True,last_error='',connect=Mock(return_value=True),disconnect=Mock(),
        read_resistance=Mock(side_effect=lambda ch:number*1000+ch),
        set_resistance=Mock(side_effect=lambda value,ch:value))
    return result


class ResistorBankTests(unittest.TestCase):
    def test_fixed_global_numbering_and_physical_mapping(self):
        boards={str(n):board(n) for n in range(1,7)}
        routes=channel_routes(boards)
        self.assertEqual(len(routes),36)
        self.assertEqual(routes[7],(boards['2'],1))
        self.assertEqual(routes[36],(boards['6'],6))
        result=operate_channels(boards,[7,36],'set',10000)
        self.assertTrue(result['ok'])
        boards['2'].set_resistance.assert_called_once_with(10000,1)
        boards['6'].set_resistance.assert_called_once_with(10000,6)
        boards['1'].set_resistance.assert_not_called()

    def test_sequence_reordering_changes_global_routes(self):
        first,second=board(2),board(1)
        routes=channel_routes({'physical1':first,'physical2':second})
        self.assertEqual(routes[1],(second,1));self.assertEqual(routes[12],(first,6))

    def test_parallel_boards_serialize_their_own_channels(self):
        first,second=board(1),board(2);barrier=threading.Barrier(2)
        def set_value(value,ch):
            if ch==1:barrier.wait(timeout=2)
            return value
        first.set_resistance.side_effect=second.set_resistance.side_effect=set_value
        result=operate_channels({'a':first,'b':second},[1,2,7,8],'set',1000)
        self.assertTrue(result['ok']);self.assertEqual(len(result['values']),4)
        self.assertEqual([c.args[1] for c in first.set_resistance.call_args_list],[1,2])

    def test_partial_failure_does_not_hide_other_results(self):
        first,second=board(1),board(2)
        first.set_resistance.side_effect=RuntimeError('mismatch')
        result=operate_channels({'a':first,'b':second},[1,7],'set',1000)
        self.assertFalse(result['ok']);self.assertEqual(result['values'],{7:1000})
        self.assertEqual(result['errors'],{1:'mismatch'})

    def test_invalid_selection_and_resistance_fail_before_writes(self):
        first=board(1)
        for channels,value in [([1,7],1000),([1],11),([1],1500010),([1,1],1000)]:
            with self.assertRaises(ValueError):operate_channels({'a':first},channels,'set',value)
        first.set_resistance.assert_not_called()

    def test_disabled_boards_keep_channel_gaps(self):
        second=board(2);second.setting['enabled']=False
        self.assertEqual(list(channel_routes({'b':second})),[])
        with self.assertRaises(ValueError):channel_routes({'a':board(1),'b':board(1)})

    def test_enabled_boards_are_contiguous_even_with_missing_sequence(self):
        first,last=board(2),board(6)
        routes=channel_routes({'last':last,'first':first})
        self.assertEqual(list(routes),list(range(1,13)))
        self.assertEqual(routes[1],(first,1))
        self.assertEqual(routes[12],(last,6))
        first.setting['enabled']=False
        self.assertEqual(channel_routes({'first':first,'last':last})[1],(last,1))


if __name__=='__main__':unittest.main()
