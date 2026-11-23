from PySide6.QtWidgets import (
    QMenu, QListWidget, QWidgetAction, QListWidgetItem, QWidget, QVBoxLayout, QLineEdit,
    QAbstractItemView, QSizePolicy, QDialog, QDialogButtonBox
)
from PySide6.QtCore import Signal, Qt, QEvent
from PySide6.QtGui import QBrush, QColor

class ScrollableMenu(QMenu):
    sheet_selected = Signal(str)

    def __init__(self, parent=None, visible_count=6, title=""):
        super().__init__(title, parent)
        self.visible_count = visible_count
        self._all_sheets = []
        self._current_sheet_title = ""

        self.container = QWidget(self)
        self.container.setMinimumWidth(260)
        self.container_layout = QVBoxLayout(self.container)
        self.container_layout.setContentsMargins(6, 6, 6, 6)
        self.container_layout.setSpacing(6)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Поиск листа...")

        self.list_widget = QListWidget()
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_widget.setSelectionMode(QAbstractItemView.SingleSelection)
        self.list_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.list_widget.setSizePolicy(QSizePolicy.Fixed, QSizePolicy.Fixed)
        self.list_widget.setMinimumWidth(240)

        self.container_layout.addWidget(self.search_field)
        self.container_layout.addWidget(self.list_widget)

        self.list_action = QWidgetAction(self)
        self.list_action.setDefaultWidget(self.container)
        self.addAction(self.list_action)

        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.search_field.textChanged.connect(self._rebuild_list)
        self.aboutToShow.connect(self._on_about_to_show)

        # QMenu перехватывает wheelEvent — перенаправляем на list_widget
        self.installEventFilter(self)

    def eventFilter(self, obj, event):
        if obj is self and event.type() == QEvent.Wheel:
            self.list_widget.wheelEvent(event)
            return True
        return super().eventFilter(obj, event)

    def _on_about_to_show(self):
        self._rebuild_list()

    def _on_item_clicked(self, item):
        self.close()
        title = item.data(Qt.UserRole)
        if title:
            self.sheet_selected.emit(title)

    def _format_sheet_title(self, title: str) -> str:
        if title == "NEW":
            return "+ Новый лист (сегодня)"
        parts = title.split("_", 1)
        if len(parts) == 2:
            return f"{parts[0]} • {parts[1]}"
        return title

    def populate(self, sheets: list):
        self._all_sheets = sheets or []
        self._rebuild_list()

    def set_current_sheet(self, title: str):
        self._current_sheet_title = (title or "").strip()
        self._rebuild_list()

    def _rebuild_list(self):
        self.list_widget.clear()
        query = self.search_field.text().strip().lower()
        sheets = self._all_sheets
        selected_item = None

        if not sheets:
            item = QListWidgetItem("Нет доступных листов")
            item.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(item)
            self._apply_height()
            return

        for sheet in sheets:
            title = sheet.get("title", "Без названия")
            pretty = self._format_sheet_title(title)
            if query and (query not in pretty.lower() and query not in title.lower()):
                continue
            item = QListWidgetItem(pretty)
            item.setData(Qt.UserRole, title)
            if title == "NEW":
                item.setForeground(QBrush(QColor("#58d68d")))
            if self._current_sheet_title and title == self._current_sheet_title:
                selected_item = item
            self.list_widget.addItem(item)

        if self.list_widget.count() == 0:
            item = QListWidgetItem("Ничего не найдено")
            item.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(item)
            self._apply_height()
            return

        self._apply_height()
        if selected_item is not None:
            self.list_widget.setCurrentItem(selected_item)

    def _apply_height(self):
        """Ограничиваем высоту списка и подгоняем ширину под содержимое."""
        row_h = self.list_widget.sizeHintForRow(0)
        if row_h <= 0:
            row_h = 24
        row_count = min(self.visible_count, max(1, self.list_widget.count()))
        h = row_h * row_count + 2 * self.list_widget.frameWidth()
        self.list_widget.setFixedHeight(h)
        self.container.setFixedHeight(h + self.search_field.sizeHint().height() + 3 * 6)

        # Ширина по самому длинному элементу, но не уже 240 и не шире 340.
        content_w = self.list_widget.sizeHintForColumn(0) + 2 * self.list_widget.frameWidth()
        if self.list_widget.verticalScrollBarPolicy() != Qt.ScrollBarAlwaysOff and row_count < self.list_widget.count():
            content_w += self.list_widget.verticalScrollBar().sizeHint().width()
        w = max(240, min(340, content_w + 24))
        self.list_widget.setFixedWidth(w)
        self.container.setFixedWidth(w + 12)


class SheetSelectDialog(QDialog):
    """Компактный диалог выбора листа со скроллом вместо QInputDialog.getItem."""

    def __init__(self, parent, sheets, current_title=""):
        super().__init__(parent)
        self.setWindowTitle("Google Sheets")
        self.setModal(True)
        self.setFixedWidth(360)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 10, 10, 10)
        layout.setSpacing(8)

        self.search_field = QLineEdit()
        self.search_field.setPlaceholderText("Поиск листа...")
        layout.addWidget(self.search_field)

        self.list_widget = QListWidget()
        self.list_widget.setVerticalScrollMode(QAbstractItemView.ScrollPerPixel)
        self.list_widget.setHorizontalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.list_widget.setVerticalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        self.list_widget.setEditTriggers(QAbstractItemView.NoEditTriggers)
        layout.addWidget(self.list_widget)

        buttons = QDialogButtonBox(QDialogButtonBox.Ok | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.list_widget.itemDoubleClicked.connect(lambda *_: self.accept())
        self.search_field.textChanged.connect(self._rebuild)

        self._titles = [s.get("title", "Без названия") if isinstance(s, dict) else str(s) for s in sheets]
        self._current = (current_title or "").strip()
        self._rebuild()

    def _rebuild(self):
        query = self.search_field.text().strip().lower()
        self.list_widget.clear()
        first = None
        for title in self._titles:
            if query and query not in title.lower():
                continue
            item = QListWidgetItem(title)
            self.list_widget.addItem(item)
            if first is None:
                first = item
            if self._current and title == self._current:
                self.list_widget.setCurrentItem(item)

        if self.list_widget.count() == 0:
            item = QListWidgetItem("Ничего не найдено")
            item.setFlags(Qt.NoItemFlags)
            self.list_widget.addItem(item)
            self.list_widget.setFixedHeight(40)
            return

        if self.list_widget.currentItem() is None and first is not None:
            self.list_widget.setCurrentItem(first)

        row_h = self.list_widget.sizeHintForRow(0)
        if row_h <= 0:
            row_h = 24
        visible = min(6, self.list_widget.count())
        self.list_widget.setFixedHeight(row_h * visible + 2 * self.list_widget.frameWidth())

    def selected_title(self):
        item = self.list_widget.currentItem()
        return item.text() if item is not None else ""