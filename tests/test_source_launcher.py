import json
import os
from pathlib import Path
import subprocess
import sys
import unittest
from unittest.mock import patch

import launch_app
from core.background_process import background_process_options


@unittest.skipUnless(os.name == 'nt', 'Windows process startup')
class SourceLauncherTests(unittest.TestCase):
    def test_launch_skips_redirector_and_keeps_feedback_disabled(self):
        with patch('launch_app.subprocess.Popen') as popen:
            popen.return_value.wait.return_value = 7
            self.assertEqual(launch_app.main(), 7)
        args, kwargs = popen.call_args
        self.assertEqual(args[0][0], sys._base_executable)
        self.assertEqual(Path(args[0][1]).name, 'main.py')
        self.assertTrue(kwargs['startupinfo'].dwFlags & 0x80)
        self.assertTrue(kwargs['creationflags'] & subprocess.CREATE_NO_WINDOW)
        self.assertEqual(Path(kwargs['env']['__PYVENV_LAUNCHER__']).parent.name, 'Scripts')

    def test_real_interpreter_preserves_venv_dependencies(self):
        with patch('launch_app.subprocess.Popen') as popen:
            launch_app.main()
        args, kwargs = popen.call_args
        probe = ('import sys,json,PySide6; '
                 'print(json.dumps([sys.prefix,PySide6.__version__]))')
        result = subprocess.run([args[0][0], '-c', probe],
                                env=kwargs['env'], capture_output=True,
                                text=True, timeout=15,
                                **background_process_options())
        self.assertEqual(result.returncode, 0, result.stderr)
        prefix, version = json.loads(result.stdout)
        self.assertEqual(Path(prefix).resolve(),
                         (Path(launch_app.__file__).parent / '.venv').resolve())
        self.assertTrue(version)


if __name__ == '__main__':
    unittest.main()
