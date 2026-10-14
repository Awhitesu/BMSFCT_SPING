"""Launch the source application without a debugger or Windows startup feedback."""
import os
from pathlib import Path
import subprocess
import sys
from core.background_process import background_process_options


def main():
    root=Path(__file__).resolve().parent
    environment=dict(os.environ)
    environment.pop('PYDEVD_USE_FRAME_EVAL',None)
    executable=sys.executable
    if os.name=='nt':
        # The venv redirector overwrites STARTUPINFO.dwFlags. Launch the real
        # interpreter while preserving the same venv selection mechanism.
        executable=getattr(sys,'_base_executable',sys.executable)
        venv_python=root/'.venv'/'Scripts'/'python.exe'
        if venv_python.is_file():
            environment['__PYVENV_LAUNCHER__']=str(venv_python)
        elif sys.prefix!=sys.base_prefix:
            environment['__PYVENV_LAUNCHER__']=sys.executable
    process=subprocess.Popen([executable,str(root/'main.py')],cwd=str(root),
                             env=environment,**background_process_options())
    return process.wait()


if __name__=='__main__':sys.exit(main())
