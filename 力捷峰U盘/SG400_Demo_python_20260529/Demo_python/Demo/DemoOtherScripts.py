from SGxxxDemoParam import ConstantParam
from Func import ExecuteScriptByProgrammer,LOGERROR
from DLLLoadManage import hDLL
from typing import Optional

def GetFileText(file_path: str) -> Optional[str]:
    try:
        with open(file_path, 'r', encoding='utf-8') as f:
            return f.read()
    except OSError as e:
        LOGERROR(f"Open {file_path} failed: {e}")
        return None

def RunLuaScriptsDirectly(lua_file: str) -> bool:
    Param = ConstantParam()
    file_content = GetFileText(lua_file)
    if file_content is None:
        return False
    success = ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, file_content)
    if not success:
        LOGERROR(f"Exec script({lua_file}) failed")
    return success

def ControlProgrammerPowerAndIO() -> None:
    Param = ConstantParam()
    power_commands = [
        "channel:setvcc(3300)",
        "channel:setvcc2ctrl(1)",  # SG400E dedicated
        "channel:vccon(100)",
        "channel:setreplystring(channel:getvcc())",  # Read VCC voltage
    ]
    
    io_script = """
    for i = 0, 5 do
        SG400_Ctrl.SetIO(i, 0)
        channel:delayms(100)
        SG400_Ctrl.SetIO(i, 1)
        channel:delayms(100)
        SG400_Ctrl.SetIO(i, 0)
    end
    """

    shutdown_commands = [
        "channel:vccoff()",
        "channel:vccqod(100)",  # SG400E dedicated, fast discharge
        "channel:setvpp(13300)",
        "channel:vppon(100)",
        "channel:setreplystring(channel:getvpp())",  # Read VPP voltage
        "channel:vppoff()",
        "channel:vppqod(100)",  # SG400E dedicated
    ]
    
    for cmd in power_commands:
        ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, cmd)
    
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, io_script)
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "SG400_Ctrl.ReleaseAllIO()")

    for cmd in shutdown_commands:
        ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, cmd)


"""
@brief DemoOtherScripts function demonstrates how to run script files and interact with the programmer.
It shows how to run a script file directly, send a script file to the programmer and execute it,
and control the power and signal pin levels of the programmer.
"""

def DemoOtherScripts() -> None:
    Param = ConstantParam()
    RunLuaScriptsDirectly("test.lua")
    
    hDLL.SendFile(bytes(Param.PROGRAMMER_TEST, encoding='utf-8'), bytes('test.lua', encoding='utf-8'), bytes('/tmp/test.lua', encoding='utf-8'))
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, 'channel:runfile("/tmp/test.lua")')
    
    ControlProgrammerPowerAndIO()