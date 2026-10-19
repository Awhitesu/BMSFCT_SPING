"""Single-line headings keep full text for accessors and a tooltip."""
from PySide6.QtCore import QSize,Qt
from PySide6.QtGui import QPainter,QPalette
from PySide6.QtWidgets import QLabel,QFrame,QSizePolicy


class ElidingLabel(QLabel):
    def __init__(self,text='',parent=None):
        super().__init__(text,parent)
        self.setTextFormat(Qt.PlainText)
        self.setSizePolicy(QSizePolicy.Ignored,QSizePolicy.Preferred)
        self.setMinimumWidth(0)
        self.setToolTip(text)

    def setText(self,text):
        super().setText(text)
        self.setToolTip(text)

    def minimumSizeHint(self):
        return QSize(0,super().minimumSizeHint().height())

    def paintEvent(self,event):
        QFrame.paintEvent(self,event)
        painter=QPainter(self)
        rect=self.contentsRect().adjusted(self.margin(),0,-self.margin(),0)
        value=self.fontMetrics().elidedText(' '.join(self.text().splitlines()),Qt.ElideRight,max(0,rect.width()))
        self.style().drawItemText(painter,rect,self.alignment(),self.palette(),
                                 self.isEnabled(),value,QPalette.WindowText)
