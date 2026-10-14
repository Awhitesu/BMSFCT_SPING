"""
@brief Demonstrate the file management function
This function tests the file management function by performing a series of file operations,
including deleting, sending, reading files, as well as obtaining the CRC32 value and checksum value
of files, etc. These operations manage files at different stages to ensure their integrity and correctness

@return void
"""
import os
from SGxxxDemoParam import ConstantParam,TARGET
from Func import ExecuteScriptByProgrammer
from DLLLoadManage import hDLL
def DemoFileManage():
    Param = ConstantParam()
    Target = TARGET()
    # deleting the project file
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "channel:delete(\"/proj/test.opj\")")
    # sending the project file
    ret = hDLL.SendFile(bytes(Param.PROGRAMMER_TEST, encoding='utf-8'), bytes('test.opj', encoding='GBK'),bytes('/proj/test.opj', encoding='GBK'))
    # Download the current device's .opj file for review. Confirm that the .opj file has been successfully sent to the device.
    ret = hDLL.GetFile( bytes(Param.PROGRAMMER_TEST, encoding='utf-8'), bytes('/proj/test.opj', encoding='GBK'), bytes('test.opj', encoding='GBK'))
    # Download the current device's log file for review. Confirm that the log file has been successfully sent to the device.
    ret = hDLL.GetFile( bytes(Param.PROGRAMMER_TEST, encoding='utf-8'), bytes('/log/ch.log', encoding='GBK'), bytes('ch.log', encoding='GBK'))
    # obtaining the CRC32 value of the project file
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "channel:filecrc32(\"/proj/test.opj\")")
    # obtaining the CRC32 value of the source file
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "get_proj_CRC(\"test.opj\")")
    # obtaining the checksum value of the project data file(opd)
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "get_proj_checksum(\"test.opj\")")
    # Get the list of project files
    ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, "channel:getfilelist(\"/proj\")")
