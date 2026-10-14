from PySide6.QtGui import QImage, QPainter, QPen, QColor
from PySide6.QtCore import Qt
import os

def generate():
    img = QImage(16, 16, QImage.Format_ARGB32)
    img.fill(Qt.transparent)
    p = QPainter(img)
    p.setRenderHint(QPainter.Antialiasing)
    # 使用鲜绿色的画笔绘制勾
    pen = QPen(QColor('#00FF00'), 2.5, Qt.SolidLine, Qt.RoundCap, Qt.RoundJoin)
    p.setPen(pen)
    p.drawLine(3, 8, 7, 12)
    p.drawLine(7, 12, 13, 3)
    p.end()
    
    dir_path = os.path.dirname(os.path.abspath(__file__))
    img.save(os.path.join(dir_path, "checkmark.png"), "PNG")

if __name__ == "__main__":
    generate()
