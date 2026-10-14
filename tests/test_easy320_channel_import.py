import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock,patch
from openpyxl import Workbook
from core.easy320_channels import normalize_channels,read_channel_excel
from devices.easy320_driver import Easy320Controller


class ChannelImportTests(unittest.TestCase):
    def test_octal_order_and_blank_names(self):
        rows=normalize_channels([(2,'DO10','八'),(3,'DO7',None),(4,'DO0','零')])
        self.assertEqual([r['index'] for r in rows],[0,7,8])
        self.assertEqual(rows[1]['name'],'')

    def test_bad_octal_and_duplicate_rejected(self):
        for rows in ([(2,'DO8','')],[(2,'DO9','')],[(2,'DO1',''),(3,'DO01','')]):
            with self.assertRaises(ValueError):normalize_channels(rows)

    def test_excel_more_than_32_channels_and_empty_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'channels.xlsx';book=Workbook();sheet=book.active
            sheet.append(['序号','名称'])
            for index in range(40):sheet.append([f'DO{index:o}', '平推气缸' if index==0 else None])
            sheet.append([None,None]);book.save(path);book.close()
            rows=read_channel_excel(path)
            self.assertEqual(len(rows),40);self.assertEqual(rows[-1]['do'],'DO47')

    def test_wrong_header_and_formula_are_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'bad.xlsx';book=Workbook();sheet=book.active
            sheet.append(['序号','名称']);sheet.append(['=1+1','alias']);book.save(path);book.close()
            with self.assertRaises(ValueError):read_channel_excel(path)

    def test_sparse_reads_use_correct_addresses_not_row_positions(self):
        with patch('devices.easy320_driver.ModbusTcpClient'):
            driver=Easy320Controller('unused');driver.is_connected=True
            driver._read_relay_range=Mock(side_effect=[[False],[True,False],[True]])
            self.assertEqual(driver.read_selected_relays([16,0,8,9]),[True,False,True,False])
            self.assertEqual([c.args for c in driver._read_relay_range.call_args_list],[(0,1),(8,2),(16,1)])

    def test_batch_controls_only_imported_addresses(self):
        with patch('devices.easy320_driver.ModbusTcpClient'):
            driver=Easy320Controller('unused');driver.write_relay=Mock(return_value=True)
            driver.read_selected_relays=Mock(return_value=[True,True])
            self.assertTrue(driver.control_selected_relays([0,8],True))
            self.assertEqual([c.args for c in driver.write_relay.call_args_list],[(0,True),(8,True)])

    def test_reading_large_config_chunks_modbus_requests(self):
        with patch('devices.easy320_driver.ModbusTcpClient'):
            driver=Easy320Controller('unused')
            driver._read_relay_range=Mock(side_effect=lambda start,count:[False]*count)
            self.assertEqual(len(driver.read_selected_relays(range(300))),300)
            self.assertEqual([c.args for c in driver._read_relay_range.call_args_list],[(0,125),(125,125),(250,50)])


if __name__=='__main__':unittest.main()
