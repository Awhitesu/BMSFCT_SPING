import ctypes
import argparse
import os
import re
import socket
import sys

import time
from ast import literal_eval
from ctypes import *
from datetime import datetime
from xml.dom.minidom import parseString

from SGxxxDemoParam import *
from DLLLoadManage import hDLL

"""
@brief Function to wait for a specific execution to complete
This function polls the programmer status to wait until an execution is completed,
either successfully, with an error, or due to timeout. It is mainly used to
ensure system stability by avoiding indefinite waiting during time-consuming executions.
@param strDevPath The device path string, specifying which device to execute on
@param eTarget Specifying which channel to execute on
@param nTimeOut Timeout value in seconds, representing the maximum waiting time
@return BOOL
"""
def WaitExecutionDone(dev_path: str, target: TARGET, timeout: int)-> bool:
	start = time.time()
	Param = ConstantParam()
	while True:
		time.sleep(0.01)  # Sleep 10 ms
		status = hDLL.QueryState(bytes(dev, encoding='utf-8'), target)
		if status == Param.STATUS_BUSY:
			if time.time() - start > timeout:
				LOGERROR("waiting script executing time out")
				return False
			time.sleep(0.1)
			continue
		elif status == Param.STATUS_ERROR:
			return False
		elif status == Param.STATUS_PASS:
			LOGINFO("PASS")
			return True
		else:
			LOGERROR("Error status 0x%08X", status)
			return False


"""
@brief
@param strDev The device path string, specifying which device to execute on
@param eTarget Specifying which channel to execute on
@param strScript Script to be executed
@return BOOL Success or failure of script execution
"""
def ExecuteScriptByProgrammer(dev: str, target: TARGET, script: str):
	# Initialize variables
	reply_buf = ctypes.create_string_buffer(0x40000)
	reply_size = ctypes.c_uint(0)
	err_msg = ctypes.create_string_buffer(256)
	Param = ConstantParam()
	script_b = bytes(script, encoding='utf-8')

	LOGINFO("CH%d execute %s", int(target), script)
	if Param.EXECUTE_MOD == Param.MODE_ASYNC:
		# Asynchronous mode
		if hDLL.ExecuteScript(bytes(dev, encoding='utf-8'), target, script_b):
			LOGINFO("CH%d executing...", int(target));
		else:
			LOGERROR("CH%d execute failed", int(target))
			if hDLL.GetErrorMsg(bytes(dev, encoding='utf-8'), target, err_msg):
				LOGERROR("CH%d error msg: %s", int(target), err_msg.value.decode())
			return False

		if not WaitExecutionDone(dev, target, 300000):
			if hDLL.GetErrorMsg(bytes(dev, encoding='utf-8'), target, err_msg):
				LOGERROR("CH%d error msg: %s", int(target), err_msg.value.decode())
			return False

		if hDLL.GetCommandReply(bytes(dev, encoding='utf-8'), target, reply_buf, len(reply_buf),
								  ctypes.byref(reply_size), 1000):
			if reply_size.value > 0:
				LOGINFO("CH%d Reply size: 0x%X", int(target), reply_size.value)
				show_len = min(reply_size.value, 0x200)
				LOGINFO("CH%d Reply data: %s", int(target), reply_buf.raw[:show_len])
	else:
		# Synchronous mode
		ret = hDLL.ExecuteScriptSync(bytes(dev, encoding='utf-8'), target, script_b, 300000,
									   reply_buf, len(reply_buf), ctypes.byref(reply_size),
									   err_msg, None)
		if ret:
			if reply_size.value>0:
				LOGINFO("CH%d Reply size: 0x%X", int(target), reply_size.value)
				show_len = min(reply_size.value, 0x200)
				LOGINFO("CH%d Reply data: %s", int(target), reply_buf.raw[:show_len])
		else:
			LOGERROR("CH%d Error: %s", int(target), err_msg.value.decode())
	if ret:
		LOGINFO("CH%d execute PASS", int(target))

	return ret


"""
@brief Continuously receive user input of scripts and execute them.
It runs in an infinite loop until the user enters "exit".
@return TRUE If the script was successfully executed or the user chose to exit.
@return FALSE If execution of the script failed.
"""
def ExecuteScriptByUserInput()-> bool:
	Param = ConstantParam()
	while True:
		try:
			script = input("Please input the script to be executed(enter 'exit' to quit)\n: ").strip()
		except EOFError:
			break
		if not script or script.startswith("#"):
			continue
		if script == "exit":
			break
		if not ExecuteScriptByProgrammer(Param.PROGRAMMER_TEST, Param.CH_TEST, script):
			LOGERROR("Exec script(%s) failed", script)
			return False
	return True