"""Keep dense forms reachable instead of compressing controls below minimums."""
from PySide6.QtWidgets import QWidget,QVBoxLayout,QScrollArea,QLayout


def protect_form_layout(page):
    old=page.layout()
    if old is None or getattr(page,'_layout_scroll',None) is not None:return
    content=QWidget()
    content.setLayout(old)
    old.setSizeConstraint(QLayout.SetMinimumSize)
    scroll=QScrollArea(page)
    scroll.setWidgetResizable(True)
    scroll.setWidget(content)
    outer=QVBoxLayout(page)
    outer.setContentsMargins(0,0,0,0)
    outer.addWidget(scroll)
    page._layout_scroll=scroll
