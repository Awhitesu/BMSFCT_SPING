"""Bound table height to visible data and ignore hidden stacked-page hints."""
from PySide6.QtCore import Qt,QSize,QTimer,QObject,QEvent
from PySide6.QtWidgets import QStackedWidget,QTabWidget,QSizePolicy,QComboBox,QHeaderView

class AdaptiveColumns(QObject):
    """Allocate the actual viewport width; never size columns from long values."""
    def __init__(self, table, weights):
        super().__init__(table)
        self.table = table
        self.weights = weights
        table.setMinimumWidth(0)
        table.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        table.setTextElideMode(Qt.ElideRight)
        header = table.horizontalHeader()
        header.setStretchLastSection(False)
        header.setMinimumSectionSize(1)
        header.setSectionResizeMode(QHeaderView.Fixed)
        table.viewport().installEventFilter(self)
        table.model().columnsInserted.connect(self.schedule)
        QTimer.singleShot(0, self.fit)

    def schedule(self, *_):
        QTimer.singleShot(0, self.fit)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.fit()
        return super().eventFilter(watched, event)

    def fit(self):
        table = self.table
        count = table.columnCount()
        weights = [self.weights[i] if i < len(self.weights) else 1 for i in range(count)]
        visible = [i for i in range(count) if not table.isColumnHidden(i)]
        total = sum(weights[i] for i in visible)
        if not total:
            return
        width = table.viewport().width()
        remaining = width
        for i in visible[:-1]:
            size = int(width * weights[i] / total)
            table.setColumnWidth(i, size)
            remaining -= size
        table.setColumnWidth(visible[-1], max(1, remaining))

def adaptive_columns(table, weights):
    table._adaptive_columns = AdaptiveColumns(table, weights)

class CurrentPageStack(QStackedWidget):
    def sizeHint(self):
        page=self.currentWidget()
        return page.sizeHint() if page else QSize(0,0)

    def minimumSizeHint(self):
        page=self.currentWidget()
        return QSize(0,max(0,page.minimumSizeHint().height())) if page else QSize(0,0)

    def heightForWidth(self, width):
        page=self.currentWidget()
        return page.heightForWidth(width) if page and page.hasHeightForWidth() else -1

    def hasHeightForWidth(self):
        page=self.currentWidget()
        return bool(page and page.hasHeightForWidth())


class CurrentPageTabs(QTabWidget):
    """Hidden parser/settings pages must not enlarge the sending workspace."""
    def hasHeightForWidth(self):
        # The current page receives the available height; the splitter's
        # preferred height must not become a scroll-area minimum height.
        return False

    def _page_size(self, minimum):
        page=self.currentWidget()
        size=(page.minimumSizeHint() if minimum else page.sizeHint()) if page else QSize(0,0)
        bar=self.tabBar().sizeHint()
        return QSize(max(size.width(),bar.width())+4,size.height()+bar.height()+4)

    def sizeHint(self):
        return self._page_size(False)

    def minimumSizeHint(self):
        return self._page_size(True)

def compact_table(table,max_rows=5,*,min_rows=0):
    table.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
    def refresh(*_):
        count=table.rowCount()
        visible=min(count,max_rows)
        body=sum(table.rowHeight(r) for r in range(visible))
        body+=max(0,min_rows-visible)*table.verticalHeader().defaultSectionSize()
        height=table.horizontalHeader().height()+body+table.frameWidth()*2
        if not count and not min_rows:height+=36
        if not min_rows or table.horizontalScrollBarPolicy()!=Qt.ScrollBarAlwaysOff:
            height+=table.horizontalScrollBar().sizeHint().height()
        table.setFixedHeight(height)
        table.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        parent=table.parentWidget()
        while parent is not None:
            if parent.layout():parent.layout().invalidate()
            parent.updateGeometry()
            parent=parent.parentWidget()
    table.model().rowsInserted.connect(lambda *_:QTimer.singleShot(0,refresh))
    table.model().rowsRemoved.connect(lambda *_:QTimer.singleShot(0,refresh))
    table.model().modelReset.connect(lambda *_:QTimer.singleShot(0,refresh))
    table.verticalHeader().sectionResized.connect(refresh)
    if min_rows:
        table.horizontalHeader().geometriesChanged.connect(lambda *_:QTimer.singleShot(0,refresh))
    QTimer.singleShot(0,refresh)

def compact_combos(root):
    for combo in root.findChildren(QComboBox):
        combo.setMaxVisibleItems(8)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
        combo.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        combo.view().setTextElideMode(Qt.ElideRight)
