# -*- coding: utf-8 -*-
import sys
import os
from PySide6.QtWidgets import (
    QApplication, QWidget, QFrame, QHBoxLayout, QVBoxLayout, QLabel, 
    QPushButton, QProgressBar, QSizePolicy
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QFont, QColor

def create_kpi_card(title: str, val_text: str, val_color: str, width: int = 110, height: int = 56) -> QFrame:
    card = QFrame()
    card.setFixedSize(width, height)
    card.setStyleSheet(f"""
        QFrame {{
            background-color: #16213E;
            border: 1px solid #2D3748;
            border-radius: 6px;
        }}
    """)
    layout = QVBoxLayout(card)
    layout.setContentsMargins(8, 4, 8, 4)
    layout.setSpacing(1)
    layout.setAlignment(Qt.AlignCenter)

    lbl_title = QLabel(title)
    lbl_title.setAlignment(Qt.AlignCenter)
    lbl_title.setStyleSheet("color: #94A3B8; font-size: 11px; font-weight: 500; border: none; background: transparent;")

    lbl_val = QLabel(val_text)
    lbl_val.setAlignment(Qt.AlignCenter)
    lbl_val.setStyleSheet(f"color: {val_color}; font-size: 20px; font-weight: bold; border: none; background: transparent; font-family: 'Segoe UI', Arial, sans-serif;")

    layout.addWidget(lbl_title)
    layout.addWidget(lbl_val)
    return card

def build_status_bar() -> QWidget:
    container = QWidget()
    container.setStyleSheet("background-color: #0F172A;")
    root_layout = QVBoxLayout(container)
    root_layout.setContentsMargins(20, 20, 20, 20)

    # 1. 标题说明
    title_lbl = QLabel("【工业标准全等高 (56px) 状态栏界面效果预览】")
    title_lbl.setStyleSheet("color: #38BDF8; font-size: 16px; font-weight: bold; margin-bottom: 10px;")
    root_layout.addWidget(title_lbl)

    # 2. 状态栏主Frame
    stats_frame = QFrame()
    stats_frame.setStyleSheet("""
        QFrame#stats_frame {
            background-color: #1A1A2E;
            border: 1px solid #2D2D4A;
            border-radius: 8px;
        }
    """)
    stats_frame.setObjectName("stats_frame")
    
    layout = QHBoxLayout(stats_frame)
    layout.setContentsMargins(12, 10, 12, 10)
    layout.setSpacing(10)

    # 4 组 KPI 卡片 (等高 56px)
    c1 = create_kpi_card("生产总数", "128", "#00E5FF", 110, 56)
    c2 = create_kpi_card("合格数", "124", "#00FF66", 110, 56)
    c3 = create_kpi_card("不合格", "4", "#FF3366", 110, 56)
    c4 = create_kpi_card("合格率", "96.88%", "#FFD700", 115, 56)

    layout.addWidget(c1)
    layout.addWidget(c2)
    layout.addWidget(c3)
    layout.addWidget(c4)

    # 5. 清零按钮 (等高 56px 卡片式按钮，管理员高亮模式)
    btn_reset = QPushButton("🔄 数据清零\n(仅管理员)")
    btn_reset.setFixedSize(110, 56)
    btn_reset.setCursor(Qt.PointingHandCursor)
    btn_reset.setStyleSheet("""
        QPushButton {
            background-color: #241D35;
            color: #FFAA33;
            border: 1px solid #FF9900;
            border-radius: 6px;
            font-size: 12px;
            font-weight: bold;
            line-height: 1.3;
        }
        QPushButton:hover {
            background-color: #33264A;
            border: 1px solid #FFB84D;
            color: #FFCC66;
        }
        QPushButton:pressed {
            background-color: #1A1426;
        }
    """)
    layout.addWidget(btn_reset)

    # 6. APP 刷写监控面板 (等高 56px)
    flash_panel = QFrame()
    flash_panel.setFixedHeight(56)
    flash_panel.setMinimumWidth(260)
    flash_panel.setStyleSheet("""
        QFrame {
            background-color: #16213E;
            border: 1px solid #2D3748;
            border-radius: 6px;
        }
    """)
    flash_layout = QVBoxLayout(flash_panel)
    flash_layout.setContentsMargins(10, 4, 10, 4)
    flash_layout.setSpacing(2)

    lbl_f1 = QLabel("APP刷写: 待启动")
    lbl_f1.setStyleSheet("color: #38BDF8; font-size: 11px; font-weight: bold; border: none; background: transparent;")
    lbl_f2 = QLabel("Record: --  |  PCAN: 等待刷写监听")
    lbl_f2.setStyleSheet("color: #94A3B8; font-size: 10px; border: none; background: transparent;")
    
    prog = QProgressBar()
    prog.setFixedHeight(5)
    prog.setRange(0, 100)
    prog.setValue(0)
    prog.setTextVisible(False)
    prog.setStyleSheet("""
        QProgressBar {
            background-color: #0F172A;
            border: none;
            border-radius: 2px;
        }
        QProgressBar::chunk {
            background-color: #00E5FF;
            border-radius: 2px;
        }
    """)

    flash_layout.addWidget(lbl_f1)
    flash_layout.addWidget(lbl_f2)
    flash_layout.addWidget(prog)
    layout.addWidget(flash_panel, 1)

    # 7. 用户账户卡片 (等高 56px)
    btn_user = QPushButton("👤 ADMIN\n(超级管理员)")
    btn_user.setFixedSize(130, 56)
    btn_user.setStyleSheet("""
        QPushButton {
            background-color: #16213E;
            color: #00FFCC;
            border: 1px solid #2D3748;
            border-radius: 6px;
            font-size: 12px;
            font-weight: bold;
            line-height: 1.3;
        }
        QPushButton:hover {
            border: 1px solid #00FFCC;
            background-color: #1D2A4F;
        }
    """)
    layout.addWidget(btn_user)

    # 8. 测试次数卡片 (等高 56px)
    lbl_counter = QLabel("⚡ 测试次数\n28 次")
    lbl_counter.setFixedSize(100, 56)
    lbl_counter.setAlignment(Qt.AlignCenter)
    lbl_counter.setStyleSheet("""
        QLabel {
            background-color: #16213E;
            color: #FFD700;
            border: 1px solid #555533;
            border-radius: 6px;
            font-size: 12px;
            font-weight: bold;
            line-height: 1.3;
        }
    """)
    layout.addWidget(lbl_counter)

    root_layout.addWidget(stats_frame)

    # 下方对比展示：普通操作员置灰态 (Operator Disabled State)
    sub_title = QLabel("【对比展示：普通操作员 (Operator) 登录时，数据清零自动置灰禁用】")
    sub_title.setStyleSheet("color: #94A3B8; font-size: 13px; font-weight: bold; margin-top: 15px; margin-bottom: 5px;")
    root_layout.addWidget(sub_title)

    stats_frame_op = QFrame()
    stats_frame_op.setStyleSheet("""
        QFrame#stats_frame_op {
            background-color: #1A1A2E;
            border: 1px solid #2D2D4A;
            border-radius: 8px;
        }
    """)
    stats_frame_op.setObjectName("stats_frame_op")
    layout_op = QHBoxLayout(stats_frame_op)
    layout_op.setContentsMargins(12, 10, 12, 10)
    layout_op.setSpacing(10)

    layout_op.addWidget(create_kpi_card("生产总数", "128", "#00E5FF", 110, 56))
    layout_op.addWidget(create_kpi_card("合格数", "124", "#00FF66", 110, 56))
    layout_op.addWidget(create_kpi_card("不合格", "4", "#FF3366", 110, 56))
    layout_op.addWidget(create_kpi_card("合格率", "96.88%", "#FFD700", 115, 56))

    btn_reset_disabled = QPushButton("🔄 数据清零\n(无权操作)")
    btn_reset_disabled.setFixedSize(110, 56)
    btn_reset_disabled.setEnabled(False)
    btn_reset_disabled.setStyleSheet("""
        QPushButton:disabled {
            background-color: #1A1A28;
            color: #555566;
            border: 1px dashed #3D3D4E;
            border-radius: 6px;
            font-size: 12px;
            font-weight: normal;
        }
    """)
    layout_op.addWidget(btn_reset_disabled)

    # 模拟 APP 监控
    flash_panel_op = QFrame()
    flash_panel_op.setFixedHeight(56)
    flash_panel_op.setMinimumWidth(260)
    flash_panel_op.setStyleSheet("background-color: #16213E; border: 1px solid #2D3748; border-radius: 6px;")
    fl_op = QVBoxLayout(flash_panel_op)
    fl_op.setContentsMargins(10, 4, 10, 4)
    fl_op.setSpacing(2)
    fl_op.addWidget(QLabel("APP刷写: 待启动", styleSheet="color: #38BDF8; font-size: 11px; font-weight: bold; border: none;"))
    fl_op.addWidget(QLabel("Record: --  |  PCAN: 等待刷写监听", styleSheet="color: #94A3B8; font-size: 10px; border: none;"))
    prog_op = QProgressBar()
    prog_op.setFixedHeight(5)
    prog_op.setValue(0)
    prog_op.setTextVisible(False)
    prog_op.setStyleSheet("QProgressBar { background-color: #0F172A; border: none; border-radius: 2px; } QProgressBar::chunk { background-color: #00E5FF; border-radius: 2px; }")
    fl_op.addWidget(prog_op)
    layout_op.addWidget(flash_panel_op, 1)

    btn_user_op = QPushButton("👤 OP001\n(操作员)")
    btn_user_op.setFixedSize(130, 56)
    btn_user_op.setStyleSheet("background-color: #16213E; color: #FFC107; border: 1px solid #2D3748; border-radius: 6px; font-size: 12px; font-weight: bold;")
    layout_op.addWidget(btn_user_op)

    lbl_counter_op = QLabel("⚡ 测试次数\n28 次")
    lbl_counter_op.setFixedSize(100, 56)
    lbl_counter_op.setAlignment(Qt.AlignCenter)
    lbl_counter_op.setStyleSheet("background-color: #16213E; color: #FFD700; border: 1px solid #555533; border-radius: 6px; font-size: 12px; font-weight: bold;")
    layout_op.addWidget(lbl_counter_op)

    root_layout.addWidget(stats_frame_op)

    container.adjustSize()
    return container

if __name__ == "__main__":
    os.environ["QT_QPA_PLATFORM"] = "windows"
    app = QApplication.instance()
    if not app:
        app = QApplication(sys.argv)
    
    widget = build_status_bar()
    widget.show()
    app.processEvents()

    # 截取渲染图
    output_dir = r"C:\Users\95403\.gemini\antigravity-ide\brain\838e2ea1-fd49-4b24-a903-3934c490f566"
    os.makedirs(output_dir, exist_ok=True)
    out_img = os.path.join(output_dir, "stats_bar_rendered_preview.png")
    
    # 强制布局渲染完整
    widget.repaint()
    app.processEvents()
    pixmap = widget.grab()
    pixmap.save(out_img, "PNG")
    print(f"[SUCCESS] UI效果图已生成: {out_img}")
    
    # 同时生成工程根目录预览
    proj_preview = os.path.join(r"c:\Users\95403\Desktop\GX_APP刷写设备", "doc", "preview")
    os.makedirs(proj_preview, exist_ok=True)
    pixmap.save(os.path.join(proj_preview, "stats_bar_rendered_preview.png"), "PNG")
    app.quit()
