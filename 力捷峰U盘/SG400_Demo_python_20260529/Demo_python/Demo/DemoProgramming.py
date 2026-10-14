from SGxxxDemoParam import *
import ctypes
import threading
from Func import ExecuteScriptByProgrammer
from DLLLoadManage import hDLL
from ctypes import *

def programming_thread(Target:TARGET) -> None:
    """Thread function: Execute chip programming task"""
    str_error = ctypes.create_string_buffer(256)
    Param = ConstantParam()
    if not hDLL.ExecuteScriptSync(
        bytes(Param.PROGRAMMER_TEST, encoding='utf-8'),
        Target,
         bytes("channel:runproject(\"test.opj\")", encoding='utf-8'),
        300000,
        None,
        0,
        None,
        str_error,
        None
    ):
        LOGERROR("CH%d Error: %s", Target, str_error)

"""
@brief Demonstrate Programming function
This function is used to test the programming process of the chip, including clearing dynamic data, setting dynamic data,
programming the chip, reading the chip, and parallel programming in asynchronous or synchronous mode.

@return void
"""
def DemoProgramming() -> None:
    Param = ConstantParam()
    Target = TARGET()
    bRet = False
    commands = [
        #Clear only dynamic data. Remove the dynamic data content that has been configured in the channel.
        "channel:clrdm()",
        #Configure the dynamic data to be added.
        #The content to be programmed must be expressed as a hexadecimal string, with the low-address bytes first and the high-address bytes last, and the total length must be an even number.
        "channel:setdm(0, \"1122\")",
        "channel:runproject(\"test.opj\")"
    ]
    
    # Execute basic commands sequentially
    for cmd in commands:
        bRet &= ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, cmd)
    
    # Get checksum
    if(bRet):
        #Set the response data. Place the string to be returned in the response buffer;
        #it can then be retrieved via the pBuffer parameter of the OpteeQ_GetCommandReply interface.
        #Typically used together with other Lua commands to fetch the return value of a command.
        bRet &= ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "channel:setreplystring(channel:getchecksum())")

    # Read chip data. The project reads the contents of the target chip’s flash;
    # the returned data can be obtained via the OpteeQ_GetCommandReply interface.
    bRet &= ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "channel:readchip(\"test.opj\", 0, 0x100)")

    # Multi-channel parallel programming
    if Param.EXECUTE_MOD == Param.MODE_ASYNC:
        # Asynchronous mode: Start and wait for all channels
        for ch in range(Target.CH4):
            if hDLL.ExecuteScript(Param.PROGRAMMER_TEST, ch + 1, "channel:runproject(\"test.opj\")"):
                LOGINFO("CH %d executing...", i+1)
        
        for ch in range(CH4):
            if not hDLL.WaitExecutionDone(Param.PROGRAMMER_TEST, ch+1, 300):
                str_error = " " * 0x100
                LOGERROR("Wait CH%d finish failed", ch+1)
                if hDLL.GetErrorMsg(Param.PROGRAMMER_TEST, ch+1, str_error):
                    LOGERROR("Error msg: %s", str_error)
            else:
                LOGINFO("Wait CH%d finish success", ch + 1);
    else:
        # Synchronous mode: Use multi-threading[1,2](@ref)
        threads = []
        for target in [Target.CH1, Target.CH2, Target.CH3, Target.CH4]:
            thread = threading.Thread(target=programming_thread, args=(target,))
            thread.start()
            threads.append(thread)
        
        for thread in threads:
            thread.join()



"""
@brief Demonstrate MES function, Note：This is an experimental feature.
This function is used to test the MES (Manufacturing Execution System) related operations.
It demonstrates how to use the ExecuteScriptByProgrammer function to interact with the MES system,
including enabling MES information logging, inserting MES information, programming the chip, and retrieving MES information.
@return void
"""
def DemoMES() -> None:
    Param = ConstantParam()
    Target = TARGET()
    mes_commands = [
        "channel:SetProgrammingInfoEnabled(true)",
        "channel:InsertProgrammingInfo(\"PCB SN\",\"ABC240405123003\")",
        "channel:runproject(\"test.opj\")",
        "channel:GetRealTimeProgrammingInfo()"
    ]
    
    for cmd in mes_commands:
        ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, cmd)
    
    # 查询历史编程信息
    strStartTime = "20250630-130000"
    strEndTime = "20250630-140000"
    strProjectName = "test.opj"
    strResult = "ERROR"
    
    history_cmd = (
        f"channel:GetHistoryProgrammingInfo("
        f"\"{strStartTime}\", \"{strEndTime}\", "
        f"\"{strProjectName}\", \"{strResult}\")"
    )
    
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, history_cmd)
