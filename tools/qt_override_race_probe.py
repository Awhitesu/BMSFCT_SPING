"""Exercise concurrent disconnectNotify lookup with valid QObject lifetimes.

No widgets or instruments are used. Connections are separate; both native
disconnects target the same live sender. Destruction happens after both join.
"""
import argparse
import faulthandler
import threading

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QApplication
import PySide6
import shiboken6


class Sender(QObject):
    signal=Signal()


def run(cycles):
    app=QApplication.instance() or QApplication([])
    app.setQuitOnLastWindowClosed(False)
    print('PYSIDE',PySide6.__version__,'SHIBOKEN',shiboken6.__version__,flush=True)
    errors=[]
    for cycle in range(cycles):
        sender=Sender();receivers=[QObject(),QObject()]
        connections=[sender.signal.connect(receiver.deleteLater) for receiver in receivers]
        barrier=threading.Barrier(3)
        def disconnect(connection):
            try:
                barrier.wait(timeout=5)
                if not QObject.disconnect(connection):errors.append('disconnect not confirmed')
            except Exception as exc:errors.append(str(exc))
        threads=[threading.Thread(target=disconnect,args=(connection,)) for connection in connections]
        for thread in threads:thread.start()
        barrier.wait(timeout=5)
        for thread in threads:thread.join(5)
        if any(thread.is_alive() for thread in threads):raise RuntimeError('join timed out')
        shiboken6.delete(sender)
        for receiver in receivers:shiboken6.delete(receiver)
        if errors:raise RuntimeError(errors)
        if (cycle+1)%100==0:print('CYCLES',cycle+1,flush=True)
    print('OVERRIDE_RACE_PROBE_PASS',cycles,flush=True)


if __name__=='__main__':
    faulthandler.enable(all_threads=True)
    parser=argparse.ArgumentParser();parser.add_argument('--cycles',type=int,default=3000)
    args=parser.parse_args();run(args.cycles)
