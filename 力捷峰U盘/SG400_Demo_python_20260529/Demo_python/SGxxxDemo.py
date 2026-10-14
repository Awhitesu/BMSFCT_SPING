#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import sys
from SGxxxDemoParam import *
from DLLLoadManage import hDLL
from Func import ExecuteScriptByUserInput
from Demo.DemoFileManage import DemoFileManage
from Demo.DemoLOGControl import DemoLOGControl
from Demo.DemoOtherScripts import DemoOtherScripts
from Demo.DemoProgramming import DemoMES,DemoProgramming

"""
@brief Load a series of apis in the DLL and obtain the handle of the DLL
@param path The path of the DLL
@return HMODULE When successful, return the module handle of the DLL; when failed, return NULL
"""
# ------------------------------------------------------------
# 7. Main function
# ------------------------------------------------------------
def main():
	Param = ConstantParam()
	# Connect the programmer
	LOGINFO("Connect to the programmer")
	if not hDLL.Connect(bytes(Param.PROGRAMMER_TEST, encoding='utf-8')):
		LOGERROR("Connect failed")

	#Logging-related functions.
	#Control the log output path, adjust the log level, and prevent all logs from being printed to the console.
	DemoLOGControl()
	
	#File Management in the Programmer
	DemoFileManage()

	#Programming control functions
	DemoProgramming()
	
	#MES function
	#DemoMES()
	
	#run script files
	DemoOtherScripts()

	Param = ConstantParam()
	ok = ExecuteScriptByUserInput()
	LOGINFO("Execute result: %s", "PASS" if ok else "ERROR")

	#Disconnect the programmer
	LOGINFO("Disconnect the programmer")
	if not hDLL.Disconnect(bytes(Param.PROGRAMMER_TEST, encoding='utf-8')):
		LOGERROR("Disconnect failed")

	return 0 if ok else 1

if __name__ == "__main__":
	sys.exit(main())