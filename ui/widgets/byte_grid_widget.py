"""
交互式可视化字节流拖拽选区组件 (ByteGridWidget)
用于在 UDS 响应报文上直观展示字节方块（序号、HEX、ASCII），
支持鼠标按住左键横向拖拽框选、点击选择以及双向联动计算偏移量与长度。
"""

from typing import List, Optional
from PySide6.QtCore import Qt, Signal, QPoint
from PySide6.QtWidgets import (
    QWidget, QFrame, QVBoxLayout, QHBoxLayout, QLabel, 
    QScrollArea, QSizePolicy
)
from PySide6.QtGui import QFont, QMouseEvent


class ByteBlock(QFrame):
    """单字节可视化胶囊块"""

    def __init__(self, index: int, byte_val: int, parent=None):
        super().__init__(parent)
        self.index = index
        self.byte_val = byte_val
        self.is_selected = False

        self.setFixedWidth(54)
        self.setFixedHeight(68)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(2, 4, 2, 4)
        layout.setSpacing(2)
        layout.setAlignment(Qt.AlignCenter)

        # 1. 索引标号
        self.lbl_idx = QLabel(f"B{index}")
        self.lbl_idx.setAlignment(Qt.AlignCenter)

        # 2. 十六进制大字
        self.lbl_hex = QLabel(f"{byte_val:02X}")
        self.lbl_hex.setAlignment(Qt.AlignCenter)

        # 3. ASCII 字符 (不可打印字符显示点号 .)
        char_repr = chr(byte_val) if 32 <= byte_val <= 126 else "."
        self.lbl_ascii = QLabel(char_repr)
        self.lbl_ascii.setAlignment(Qt.AlignCenter)

        layout.addWidget(self.lbl_idx)
        layout.addWidget(self.lbl_hex)
        layout.addWidget(self.lbl_ascii)

        self.update_style()

    def set_selected(self, selected: bool):
        if self.is_selected != selected:
            self.is_selected = selected
            self.update_style()

    def update_style(self):
        if self.is_selected:
            self.setStyleSheet("""
                QFrame {
                    background-color: #0284C7;
                    border: 2px solid #38BDF8;
                    border-radius: 6px;
                }
                QLabel {
                    background: transparent;
                }
            """)
            self.lbl_idx.setStyleSheet("font-size: 10px; color: #E0F2FE; font-weight: bold;")
            self.lbl_hex.setStyleSheet("font-size: 14px; font-weight: bold; color: #FFFFFF; font-family: Consolas, monospace;")
            self.lbl_ascii.setStyleSheet("font-size: 11px; color: #BAE6FD; font-weight: bold;")
        else:
            self.setStyleSheet("""
                QFrame {
                    background-color: #111827;
                    border: 1px solid #334155;
                    border-radius: 6px;
                }
                QFrame:hover {
                    border: 1px solid #38BDF8;
                    background-color: #1E293B;
                }
                QLabel {
                    background: transparent;
                }
            """)
            self.lbl_idx.setStyleSheet("font-size: 10px; color: #64748B;")
            self.lbl_hex.setStyleSheet("font-size: 14px; font-weight: bold; color: #E2E8F0; font-family: Consolas, monospace;")
            self.lbl_ascii.setStyleSheet("font-size: 11px; color: #94A3B8;")


class ByteGridWidget(QWidget):
    """
    字节流交互式横向容器
    发射信号: range_selected(offset, length, selected_bytes)
    """
    range_selected = Signal(int, int, bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._raw_bytes = b""
        self._blocks: List[ByteBlock] = []
        self._drag_start_idx: Optional[int] = None
        self._is_dragging = False

        self._start_offset = 0
        self._sel_length = 0

        # 主布局
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        # 横向滚动区域
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFixedHeight(88)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background-color: #090E17;
                border: 1px solid #1E293B;
                border-radius: 8px;
            }
            QScrollBar:horizontal {
                height: 8px;
                background: #090E17;
                border-radius: 4px;
            }
            QScrollBar::handle:horizontal {
                background: #334155;
                border-radius: 4px;
                min-width: 20px;
            }
            QScrollBar::handle:horizontal:hover {
                background: #0E7490;
            }
        """)

        # 内部横向容器
        self.inner_widget = QWidget()
        self.inner_widget.setStyleSheet("background: transparent;")
        self.blocks_layout = QHBoxLayout(self.inner_widget)
        self.blocks_layout.setContentsMargins(8, 8, 8, 8)
        self.blocks_layout.setSpacing(6)
        self.blocks_layout.setAlignment(Qt.AlignLeft | Qt.AlignVCenter)

        self.scroll_area.setWidget(self.inner_widget)
        main_layout.addWidget(self.scroll_area)

        # 占位提示标签 (无数据时展示)
        self.lbl_empty = QLabel("请在上方输入请求或期望响应，即可在此直接拖拽框选字节")
        self.lbl_empty.setStyleSheet("color: #64748B; font-size: 12px; font-style: italic;")
        self.blocks_layout.addWidget(self.lbl_empty)

    def set_hex_string(self, hex_text: str):
        """传入十六进制字符串 (如 '62 01 01 0E 10') 并刷新字节块"""
        clean_text = hex_text.replace("??", "00").replace("**", "00").replace(",", " ")
        tokens = clean_text.split()
        data_list = []
        for t in tokens:
            try:
                data_list.append(int(t, 16) & 0xFF)
            except ValueError:
                pass
        self.set_bytes(bytes(data_list))

    def set_bytes(self, raw_data: bytes):
        """传入二进制字节并刷新所有 Block"""
        self._raw_bytes = raw_data
        self._clear_blocks()

        if not raw_data:
            self.lbl_empty.setVisible(True)
            self._start_offset = 0
            self._sel_length = 0
            return

        self.lbl_empty.setVisible(False)
        for i, b in enumerate(raw_data):
            block = ByteBlock(i, b, self.inner_widget)
            self.blocks_layout.addWidget(block)
            self._blocks.append(block)

        # 如果已有选区，重新应用高亮
        if self._sel_length > 0:
            self._apply_selection(self._start_offset, self._sel_length, emit_signal=False)

    def _clear_blocks(self):
        for block in self._blocks:
            block.setParent(None)
            block.deleteLater()
        self._blocks.clear()

    def set_selection(self, offset: int, length: int, emit_signal: bool = True):
        """通过代码设定选区 (如加载配方或双击表格时)"""
        self._apply_selection(offset, length, emit_signal=emit_signal)

    def _apply_selection(self, offset: int, length: int, emit_signal: bool = True):
        total = len(self._blocks)
        if total == 0:
            self._start_offset = offset
            self._sel_length = length
            return

        # 边界修剪
        if offset >= total:
            # 超出当前可用字节流，不选任何 block，但保留请求的 offset/length
            self._start_offset = offset
            self._sel_length = length
            for block in self._blocks:
                block.set_selected(False)
            if emit_signal:
                self.range_selected.emit(offset, length, b"")
            return

        start = max(0, offset)
        if length <= 0:
            end = total - 1
            actual_len = total - start
        else:
            end = min(start + length - 1, total - 1)
            actual_len = end - start + 1

        self._start_offset = start
        self._sel_length = actual_len

        for i, block in enumerate(self._blocks):
            block.set_selected(start <= i <= end)

        selected_chunk = self._raw_bytes[start : start + actual_len]
        if emit_signal:
            self.range_selected.emit(start, actual_len, selected_chunk)

    def _get_block_index_at_pos(self, global_pos: QPoint) -> Optional[int]:
        for i, block in enumerate(self._blocks):
            local_pos = block.mapFromGlobal(global_pos)
            if block.rect().contains(local_pos):
                return i
        return None

    def mousePressEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton:
            idx = self._get_block_index_at_pos(event.globalPosition().toPoint())
            if idx is not None:
                self._is_dragging = True
                self._drag_start_idx = idx
                self._apply_selection(idx, 1)

    def mouseMoveEvent(self, event: QMouseEvent):
        if self._is_dragging and self._drag_start_idx is not None:
            idx = self._get_block_index_at_pos(event.globalPosition().toPoint())
            if idx is not None:
                start = min(self._drag_start_idx, idx)
                end = max(self._drag_start_idx, idx)
                length = end - start + 1
                self._apply_selection(start, length)

    def mouseReleaseEvent(self, event: QMouseEvent):
        if event.button() == Qt.LeftButton and self._is_dragging:
            self._is_dragging = False
            self._drag_start_idx = None
            # 最终确认发射一次信号
            selected_chunk = self._raw_bytes[self._start_offset : self._start_offset + self._sel_length]
            self.range_selected.emit(self._start_offset, self._sel_length, selected_chunk)

    def get_selected_range(self):
        """返回 (offset, length, selected_bytes)"""
        chunk = self._raw_bytes[self._start_offset : self._start_offset + self._sel_length]
        return self._start_offset, self._sel_length, chunk
