import unittest
from PySide6.QtWidgets import QApplication
from ui.dialogs.step_dialog import StepDialog
from core.device_steps import DEVICES,ACTIONS,encode,decode


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


if __name__=='__main__':unittest.main()
