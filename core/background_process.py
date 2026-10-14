import os
import subprocess


def background_process_options():
    """Background CLI processes must not show consoles or request startup feedback."""
    if os.name != 'nt':return {}
    info=subprocess.STARTUPINFO()
    info.dwFlags|=subprocess.STARTF_USESHOWWINDOW | 0x80  # STARTF_FORCEOFFFEEDBACK
    info.wShowWindow=0
    return {'startupinfo':info,'creationflags':subprocess.CREATE_NO_WINDOW}
