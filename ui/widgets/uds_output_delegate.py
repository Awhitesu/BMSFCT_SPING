"""A visible, whole-cell toggle for the exclusive UDS output column."""
from PySide6.QtCore import Qt,QEvent,QPointF,QRectF
from PySide6.QtGui import QColor,QPen,QPainterPath,QPainter
from PySide6.QtWidgets import QStyledItemDelegate,QStyleOptionViewItem,QStyle,QApplication


class UDSOutputDelegate(QStyledItemDelegate):
    def paint(self,painter,option,index):
        background=QStyleOptionViewItem(option)
        self.initStyleOption(background,index)
        background.features &= ~QStyleOptionViewItem.HasCheckIndicator
        background.text=''
        style=background.widget.style() if background.widget else QApplication.style()
        style.drawControl(QStyle.CE_ItemViewItem,background,painter,background.widget)
        painter.save()
        try:
            size=max(4,min(22,option.rect.height()-8))
            center=option.rect.center()
            left,top=center.x()-size/2,center.y()-size/2
            painter.setRenderHint(QPainter.Antialiasing)
            enabled=bool(index.flags() & Qt.ItemIsEnabled)
            painter.setPen(QPen(QColor('#FFFFFF' if enabled else '#64748B'),2))
            painter.setBrush(Qt.NoBrush)
            painter.drawRoundedRect(QRectF(left,top,size,size),3,3)
            if index.data(Qt.CheckStateRole) in (Qt.Checked,Qt.Checked.value):
                path=QPainterPath(QPointF(left+size*.2,top+size*.53))
                path.lineTo(left+size*.43,top+size*.76)
                path.lineTo(left+size*.83,top+size*.25)
                painter.setPen(QPen(QColor('#22C55E'),3,Qt.SolidLine,Qt.RoundCap,Qt.RoundJoin))
                painter.drawPath(path)
        finally:painter.restore()

    def editorEvent(self,event,model,option,index):
        if not (index.flags() & Qt.ItemIsEnabled and index.flags() & Qt.ItemIsUserCheckable):return False
        if event.type()==QEvent.MouseButtonDblClick:return True
        if event.type()==QEvent.MouseButtonRelease:
            if event.button()!=Qt.LeftButton or not option.rect.contains(event.position().toPoint()):return False
        elif event.type()==QEvent.KeyPress:
            if event.key() not in (Qt.Key_Space,Qt.Key_Select):return False
        else:return False
        value=Qt.Unchecked if index.data(Qt.CheckStateRole) in (Qt.Checked,Qt.Checked.value) else Qt.Checked
        return model.setData(index,value,Qt.CheckStateRole)
