import os
from ctypes import *
from SGxxxDemoParam import *


class OpteeQDLL:
    def __init__(self):
        self.Param = ConstantParam()
        try:
            self.OpteeQDLL = cdll.LoadLibrary(os.path.abspath("OpteeQDLL_32.dll"))
            if self.OpteeQDLL is None:
                LOGERROR("Load %s failed!", "OpteeQDLL_32.dll")
                return
        except:
            try:
                self.OpteeQDLL = cdll.LoadLibrary(os.path.abspath("OpteeQDLLx64.dll"))
                if self.OpteeQDLL is None:
                    LOGERROR("Load %s failed!", "OpteeQDLLx64.dll")
                    return
            except:
                LOGERROR("Dll load failed!")

    #Get device information. The return data can be obtained through the OpteeQ_GetCommandReply interface.
    def SendCommand(self, strDev, eTarget, strCommand):
        self.OpteeQDLL.OpteeQ_SendCommand.restype = c_bool
        self.OpteeQDLL.OpteeQ_SendCommand.argtypes = [c_char_p, c_int, c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_SendCommand(strDev, eTarget, strCommand)
        return Ret

    #   Send programming command
    def ExecuteScript(self, strDev, eTarget, strScript):
        self.OpteeQDLL.OpteeQ_ExecuteScript.restype = c_bool
        self.OpteeQDLL.OpteeQ_ExecuteScript.argtypes = [c_char_p, c_int, c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_ExecuteScript(strDev, eTarget, strScript)
        return Ret

    #   Send the script to the programmer for execution and automatically wait for the script to complete.
    def ExecuteScriptSync(self, strDev, eTarget, strScript,nTimeoutMs,pReplyBuffer,nReplyBufferSize,pnReplyedSize,strErrorMsg,callback):
        self.OpteeQDLL.OpteeQ_ExecuteScriptSync.restype = c_bool
        self.OpteeQDLL.OpteeQ_ExecuteScriptSync.argtypes = [c_char_p, c_int, c_char_p,c_uint,c_char_p,c_uint,POINTER(c_uint),c_char_p,c_void_p]
        Ret = self.OpteeQDLL.OpteeQ_ExecuteScriptSync(strDev, eTarget, strScript,nTimeoutMs,pReplyBuffer,nReplyBufferSize,pnReplyedSize,strErrorMsg,callback)
        return Ret

    #   Force stop the currently executing script
    def StopExecute(self, strDev, eTarget):
        self.OpteeQDLL.OpteeQ_StopExecute.restype = c_bool
        self.OpteeQDLL.OpteeQ_StopExecute.argtypes = [c_char_p, c_int]
        Ret = self.OpteeQDLL.OpteeQ_StopExecute(strDev, eTarget)
        return Ret

    #   Query single channel status
    def QueryState(self, strDev, eTarget):
        self.OpteeQDLL.OpteeQ_QueryState.restype = c_uint32
        self.OpteeQDLL.OpteeQ_QueryState.argtypes = [c_char_p, c_int]
        Ret = self.OpteeQDLL.OpteeQ_QueryState(strDev, eTarget)
        return Ret

    #   Query all channel statuses
    def QueryAllState(self, strDev, dwState):
        self.OpteeQDLL.OpteeQ_QueryAllState.restype = c_bool
        self.OpteeQDLL.OpteeQ_QueryAllState.argtypes = [c_char_p, POINTER(c_uint)]
        Ret = self.OpteeQDLL.OpteeQ_QueryAllState(strDev, dwState)
        return Ret

    #   Query error message string
    def GetErrorMsg(self, strDev, eTarget, strErrorMsg):
        self.OpteeQDLL.OpteeQ_GetErrorMsg.restype = c_bool
        self.OpteeQDLL.OpteeQ_GetErrorMsg.argtypes = [c_char_p, c_int, c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_GetErrorMsg(strDev, eTarget, strErrorMsg)
        return Ret

    #   Get data returned after command execution
    def GetCommandReply(self, strDev, eTarget, pBuffer, nBufferSize, pnBufferRecvSize, nTimeout):
        self.OpteeQDLL.OpteeQ_GetCommandReply.restype = c_bool
        self.OpteeQDLL.OpteeQ_GetCommandReply.argtypes = [c_char_p, c_int, POINTER(c_uint8), c_uint,POINTER(c_uint), c_uint]
        Ret = self.OpteeQDLL.OpteeQ_GetCommandReply(strDev, eTarget, pBuffer, nBufferSize, pnBufferRecvSize, nTimeout)
        return Ret

    #   Connect to programmer
    def Connect(self, strDev):
        self.OpteeQDLL.OpteeQ_Connect.restype = c_bool
        self.OpteeQDLL.OpteeQ_Connect.argtypes = [c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_Connect(strDev)
        return Ret

    #   Disconnect programmer
    def Disconnect(self, strDev):
        self.OpteeQDLL.OpteeQ_Disconnect.restype = c_bool
        self.OpteeQDLL.OpteeQ_Disconnect.argtypes = [c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_Disconnect(strDev)
        return Ret

    #   Send file to programmer (e.g., project file)
    def SendFile(self, strDev, strLocalFile, strTargetFile):
        self.OpteeQDLL.OpteeQ_SendFile.restype = c_bool
        self.OpteeQDLL.OpteeQ_SendFile.argtypes = [c_char_p, c_char_p, c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_SendFile(strDev, strLocalFile, strTargetFile)
        return Ret

    #   Read file from programmer (read project file/log file)
    def GetFile(self, strDev, strLocalFile, strTargetFile):
        self.OpteeQDLL.OpteeQ_GetFile.restype = c_bool
        self.OpteeQDLL.OpteeQ_GetFile.argtypes = [c_char_p, c_char_p, c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_GetFile(strDev, strLocalFile, strTargetFile)
        return Ret


    def SendKey(self, strDev, key, length):
        self.OpteeQDLL.OpteeQ_SendKey.restype = c_bool
        self.OpteeQDLL.OpteeQ_SendKey.argtypes = [c_char_p, c_char_p, c_uint]
        Ret = self.OpteeQDLL.OpteeQ_SendKey(strDev, key, length)
        return Ret

    #   Set log file for DLL log data. Default log file is OpteeQDLL.log. Call this function to specify a new file if needed
    def StartLogToFile(self, szFilePath):
        self.OpteeQDLL.OpteeQ_StartLogToFile.restype = c_bool
        self.OpteeQDLL.OpteeQ_StartLogToFile.argtypes = [c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_StartLogToFile(szFilePath)
        return Ret

    #   Set DLL logging level (0~5). Levels: Debug(0), Info(1), Warning(2), Error(3), System(4), Disable(5). Lower levels log more information.
    def SetLogLevel(self, nLevel):
        self.OpteeQDLL.OpteeQ_SetLogLevel.restype = c_bool
        self.OpteeQDLL.OpteeQ_SetLogLevel.argtypes = [c_int]
        Ret = self.OpteeQDLL.OpteeQ_SetLogLevel(nLevel)
        return Ret

    #   Stop DLL logging
    def StopLog(self):
        self.OpteeQDLL.OpteeQ_StopLog.restype = c_bool
        Ret = self.OpteeQDLL.OpteeQ_StopLog()
        return Ret

    #   stop log to the console window
    def StopLogToConsole(self):
        self.OpteeQDLL.OpteeQ_StopLogToConsole.restype = c_bool
        Ret = self.OpteeQDLL.OpteeQ_StopLogToConsole()
        return Ret

    #   Capture real-time log messages from programmer and save to specified file
    def ReceiveDevLog(self, strDev, file):
        self.OpteeQDLL.OpteeQ_ReceiveDevLog.restype = c_bool
        self.OpteeQDLL.OpteeQ_ReceiveDevLog.argtypes = [c_char_p, c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_ReceiveDevLog(strDev, file)
        return Ret

    #   Stop capturing real-time logs from programmer
    def StopDevLog(self, strDev):
        self.OpteeQDLL.OpteeQ_StopDevLog.restype = c_bool
        self.OpteeQDLL.OpteeQ_StopDevLog.argtypes = [c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_StopDevLog(strDev)
        return Ret

    #   Change real-time log storage location for specified programmer
    def ChangeLogFile(self, strDev, file):
        self.OpteeQDLL.OpteeQ_ChangeLogFile.restype = c_bool
        self.OpteeQDLL.OpteeQ_ChangeLogFile.argtypes = [c_char_p, c_char_p]
        Ret = self.OpteeQDLL.OpteeQ_ChangeLogFile(strDev, file)
        return Ret

hDLL= OpteeQDLL()