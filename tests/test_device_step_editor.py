import unittest
import tempfile
from pathlib import Path
from PySide6.QtWidgets import QApplication
from ui.dialogs.step_dialog import StepDialog
from core.device_steps import DEVICES,ACTIONS,encode,decode
from data.db_manager import DBManager


app=QApplication.instance() or QApplication([])


class DeviceEditorTests(unittest.TestCase):
    def test_every_action_can_be_created_saved_and_reopened(self):
        source=StepDialog();restored=StepDialog()
        try:
            for key,specs in ACTIONS.items():
                group,label=DEVICES[key]
                for spec in specs:
                    with self.subTest(device=key,action=spec.key):
                        if key == 'simulator' and spec.key != 'current_batch':
                            # Historical actions remain editable, but are no longer offered for new steps.
                            source._load_data(dict(device=label,action=spec.label,params=encode(key,spec.key,{}),
                                                   type='读取仪表' if spec.read else '设置仪表'))
                        else:
                            source.category_combo.setCurrentText('设备操作')
                            source.sub_category_combo.setCurrentText(group)
                            source.device_combo.setCurrentText(label)
                            source.action_combo.setCurrentText(spec.label)
                            source.action_combo.activated.emit(source.action_combo.currentIndex())
                        expected = source.simulator_read_form if spec.key == 'current_batch' else source.device_step_form
                        self.assertEqual(source.param_stack.currentWidget(),expected)
                        source.cb_judgment.setChecked(spec.read)
                        source.fail_strategy_combo.setCurrentText('重试3次')
                        data=source.get_data()
                        self.assertEqual(decode(data['params'])[:2],(key,spec))
                        restored._load_data(data)
                        self.assertTrue(restored.btn_ok.isEnabled())
                        self.assertEqual(restored.get_data(),data)
        finally:source.close();restored.close()

    def test_specific_board_channel_and_precision_survive_reload(self):
        data={'device':DEVICES['resistor'][1],'action':'设置阻值',
              'params':encode('resistor','resistance',{'board':6,'channel':6,'resistance':1500000}),
              'type':'设置仪表','is_judgment':False,'fail_strategy':'失败停止'}
        page=StepDialog(step_data=data)
        self.assertEqual(decode(page.get_data()['params'])[2]['board'],6)
        self.assertEqual(page.device_step_form.controls['channel'].value(),6)
        page.close()

    def test_existing_device_actions_are_retained(self):
        page=StepDialog()
        for key,label in [('simulator','快捷配置'),('dut_power','设置参数'),
                          ('easy320','闭合勾选通道'),('ca550','参数设置')]:
            group,device=DEVICES[key]
            page.sub_category_combo.setCurrentText(group);page.device_combo.setCurrentText(device)
            self.assertGreaterEqual(page.action_combo.findText(label),0)
        page.close()

    def test_simulator_low_and_channels_survive_recipe_save_and_reopen(self):
        source=StepDialog();restored=None
        try:
            source.sub_category_combo.setCurrentText('模拟电池')
            source.action_combo.setCurrentText('快捷配置')
            source.sim_batch_volt.setValue(2.5)
            source.sim_batch_curr.setValue(.5)
            source.sim_batch_output.setCurrentText('OFF')
            source.sim_batch_range.setCurrentIndex(1)
            source.sim_batch_channels.setText('1,7,25')
            source.cb_judgment.setChecked(True)
            source.fail_strategy_combo.setCurrentText('忽略继续')
            saved=source.get_data()
            self.assertIn('Range:LOW',saved['params'])
            with tempfile.TemporaryDirectory() as directory:
                db=DBManager(str(Path(directory)/'test.db'))
                try:
                    recipe={'name':'range_restore','items':[{'name':'simulator','sub_steps':[saved]}]}
                    self.assertTrue(db.save_recipe_json('range_restore',recipe))
                    loaded=db.load_recipe_json('range_restore')['items'][0]['sub_steps'][0]
                    restored=StepDialog(step_data=loaded)
                    self.assertEqual(restored.sim_batch_range.currentData(),'LOW')
                    self.assertEqual(restored.sim_batch_curr.value(),1)
                    self.assertEqual(restored.sim_batch_channels.text(),'1,7,25')
                    self.assertEqual(restored.get_data(),{k:v for k,v in loaded.items() if k!='step_id'})
                finally:db.close()
        finally:
            if restored:restored.close()
            source.close()

    def test_historical_quick_configuration_restores_low_and_resets_missing_defaults(self):
        data=dict(device=DEVICES['simulator'][1],action='快捷批量配置',
                  params='3.2V / 0.5mA / ON / Range:LOW / CH:2',type='设置仪表')
        page=StepDialog(step_data=data)
        try:
            self.assertEqual(page.action_combo.currentText(),'快捷配置')
            self.assertEqual(page.sim_batch_range.currentData(),'LOW')
            self.assertEqual(page.sim_batch_curr.value(),1)
            self.assertEqual(page.sim_batch_channels.text(),'2')
            page._load_data(dict(data,params='3.2V / 1000mA / ON / Range:HIGH / CH:ALL'))
            self.assertEqual(page.sim_batch_range.currentData(),'HIGH')
            self.assertEqual(page.sim_batch_channels.text(),'ALL')
            page._load_data(data)
            page._load_data(dict(data,params='3.2V / 1000mA / ON'))
            self.assertEqual(page.sim_batch_range.currentData(),'HIGH')
            self.assertEqual(page.sim_batch_channels.text(),'ALL')
        finally:page.close()


if __name__=='__main__':unittest.main()
