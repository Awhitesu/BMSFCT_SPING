"""Bound table height to visible data and ignore hidden stacked-page hints."""
from PySide6.QtCore import Qt,QSize,QTimer,QObject,QEvent
from PySide6.QtWidgets import QStackedWidget,QTabWidget,QSizePolicy,QComboBox,QHeaderView,QGridLayout
from shiboken6 import isValid

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
        QTimer.singleShot(0, self, self.fit)

    def schedule(self, *_):
        if isValid(self) and isValid(self.table):
            QTimer.singleShot(0, self, self.fit)

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.Show):
            self.fit()
        return super().eventFilter(watched, event)

    def fit(self):
        table = self.table
        if not isValid(table):
            return
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


class ResponsiveFieldsLayout(QGridLayout):
    """Pair labeled fields when both editors fit comfortably in the page."""
    def __init__(self, parent):
        super().__init__(parent)
        self.setHorizontalSpacing(12)
        self.fields = []
        self._columns = None
        parent.installEventFilter(self)

    def set_fields(self, tip, fields):
        self.tip = tip
        self.fields = fields
        self._columns = None
        self._arrange()

    def _arrange(self):
        if not hasattr(self, 'tip'):
            return
        margins = self.contentsMargins()
        required = margins.left() + margins.right() + self.horizontalSpacing() * 3
        for column in range(2):
            pairs = self.fields[column::2]
            required += max((label.minimumSizeHint().width() for label, _ in pairs), default=0)
            required += max((max(120, control.minimumSizeHint().width()) for _, control in pairs), default=0)
        columns = 2 if len(self.fields) > 1 and self.parentWidget().contentsRect().width() >= max(800, required) else 1
        if columns == self._columns:
            return
        self._columns = columns
        while self.count():
            self.takeAt(0)
        self.addWidget(self.tip, 0, 0, 1, columns * 2)
        for index, (label, control) in enumerate(self.fields):
            row, column = divmod(index, columns)
            self.addWidget(label, row + 1, column * 2)
            self.addWidget(control, row + 1, column * 2 + 1)
        for column in range(4):
            self.setColumnStretch(column, int(column < columns * 2 and column % 2 == 1))
        self.parentWidget().updateGeometry()

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Resize, QEvent.LayoutRequest, QEvent.StyleChange, QEvent.FontChange):
            self._arrange()
        return super().eventFilter(watched, event)


class CurrentPageStack(QStackedWidget):
    """Size the parameter area from its visible page, including wrapped text."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSizePolicy(QSizePolicy.Expanding, QSizePolicy.Maximum)
        self.currentChanged.connect(self._page_changed)

    def _page_changed(self, *_):
        page = self.currentWidget()
        vertical = (QSizePolicy.Expanding if page and page.sizePolicy().expandingDirections() & Qt.Vertical
                    else QSizePolicy.Maximum)
        self.setSizePolicy(QSizePolicy.Expanding, vertical)
        self.updateGeometry()

    def _page_size(self, minimum=False):
        page = self.currentWidget()
        if page is None:
            return QSize(0, 0)
        size = page.minimumSizeHint() if minimum else page.sizeHint()
        height = max(0, size.height(), page.minimumHeight())
        if page.hasHeightForWidth():
            width = self.contentsRect().width()
            layout = page.layout()
            wrapped = layout.minimumHeightForWidth(width) if minimum and layout else page.heightForWidth(width)
            height = max(height, wrapped)
        return QSize(0 if minimum else size.width(), height)

    def sizeHint(self):
        return self._page_size()

    def minimumSizeHint(self):
        return self._page_size(True)

    def hasHeightForWidth(self):
        # QStackedLayout.heightForWidth() includes hidden pages. Let the outer
        # layout use our current-page hints instead of that aggregate height.
        return False

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if event.size().width() != event.oldSize().width():
            self.updateGeometry()

    def event(self, event):
        result = super().event(event)
        if event.type() == QEvent.LayoutRequest:
            self.updateGeometry()
        return result


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
        if not isValid(table):
            return
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
    def schedule(*_):
        if isValid(table):
            QTimer.singleShot(0,table,refresh)
    table.model().rowsInserted.connect(schedule)
    table.model().rowsRemoved.connect(schedule)
    table.model().modelReset.connect(schedule)
    table.verticalHeader().sectionResized.connect(refresh)
    if min_rows:
        table.horizontalHeader().geometriesChanged.connect(schedule)
    QTimer.singleShot(0,table,refresh)

def compact_combos(root):
    for combo in root.findChildren(QComboBox):
        combo.setMaxVisibleItems(8)
        combo.setSizeAdjustPolicy(QComboBox.AdjustToMinimumContentsLengthWithIcon)
        combo.setMinimumContentsLength(10)
        combo.setSizePolicy(QSizePolicy.Expanding,QSizePolicy.Fixed)
        combo.view().setTextElideMode(Qt.ElideRight)
