"""Keep wheel scrolling from changing instrument and recipe parameters."""
from PySide6.QtCore import QObject,QEvent,QPointF
from PySide6.QtGui import QWheelEvent
from PySide6.QtWidgets import (QApplication,QAbstractSpinBox,QComboBox,
                              QLineEdit,QAbstractScrollArea)


class ParameterWheelGuard(QObject):
    def eventFilter(self,watched,event):
        if event.type()!=QEvent.Wheel:return False
        control=watched
        if isinstance(control,QLineEdit):control=control.parentWidget()
        if not isinstance(control,(QAbstractSpinBox,QComboBox)):return False
        # Block value changes even when the field retains focus after editing.
        # The open dropdown's list/viewport is not a parameter control and can
        # still scroll normally.
        parent=control.parentWidget()
        while parent is not None and not isinstance(parent,QAbstractScrollArea):
            parent=parent.parentWidget()
        if parent is not None:
            viewport=parent.viewport()
            forwarded=QWheelEvent(QPointF(viewport.mapFromGlobal(event.globalPosition().toPoint())),
                event.globalPosition(),event.pixelDelta(),event.angleDelta(),
                event.buttons(),event.modifiers(),event.phase(),event.inverted(),
                event.source(),event.pointingDevice())
            QApplication.sendEvent(viewport,forwarded)
        event.accept()
        return True


def install_parameter_wheel_guard(app=None):
    app=app or QApplication.instance()
    if app is None:return None
    guard=getattr(app,'_parameter_wheel_guard',None)
    if guard is None:
        guard=ParameterWheelGuard(app)
        app.installEventFilter(guard)
        app._parameter_wheel_guard=guard
    return guard
