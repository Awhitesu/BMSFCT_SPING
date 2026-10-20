#!/usr/bin/env python3
# -*- coding: utf-8 -*-
import sys


"""
The ConstantParam structure records a set of 
constant system-configuration items.
"""
class ConstantParam():
    def __init__(self):
        super().__init__()
        self.PROGRAMMER_TEST = 'LAN192.168.0.192:10023'
        self.CH_TEST         = TARGET().CH1          # Corresponds to CH1 in C

        self.MODE_ASYNC = 0
        self.MODE_SYNC  = 1
        self.EXECUTE_MOD = self.MODE_SYNC

        self.STATUS_BUSY  = 1
        self.STATUS_READY = 2
        self.STATUS_ERROR = 3
        self.STATUS_PASS  = 4

"""
The TARGET structure stores the channel 
information of the target programmer.
"""
class TARGET():
    def __init__(self):
        super().__init__()
        self.KERNAL = 0
        self.CH1    = 1
        self.CH2    = 2
        self.CH3    = 3
        self.CH4    = 4
        self.CH5    = 5
        self.CH6    = 6
        self.CH7    = 7
        self.CH8    = 8
        self.CH9    = 9
        self.CH10   = 10
        self.CH11   = 11
        self.CH12   = 12
        self.CH13   = 13
        self.CH14   = 14
        self.CH15   = 15
        self.CH16   = 16

#LOGINFO function is used to output informational prompts during execution.
def LOGINFO(fmt, *args):
    print(f"[info]({sys._getframe(1).f_lineno:3d}):{fmt % args}")

#The LOGERROR function outputs error messages encountered during execution.
def LOGERROR(fmt, *args):
    print(f"[err ]({sys._getframe(1).f_lineno:3d}):{fmt % args}", file=sys.stderr)
