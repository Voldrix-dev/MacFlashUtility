import json
import os
import re
import qtawesome as qta

from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QBrush, QFont, QPainter, QFontMetrics
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QPushButton, QSpinBox,
    QTableWidget, QTableWidgetItem, QHeaderView, QColorDialog, QLineEdit,
    QDialogButtonBox, QAbstractItemView, QMessageBox, QInputDialog,
    QStyledItemDelegate, QTabWidget, QWidget,
)

from style import opstion_style
from templates import safe_format

COND_TYPES = ["BLANK", "NOT_BLANK", "TEXT_CONTAINS", "TEXT_NOT_CONTAINS"]
DEFAULT_WIDTH = 160


def _col_letter(idx):
    """0 -> A, 1 -> B, 25 -> Z, 26 -> AA."""
    s = ""
    idx += 1
    while idx:
        idx, rem = divmod(idx - 1, 26)
        s = chr(65 + rem) + s
    return s


def _letter_to_idx(text):
    """A -> 0, B -> 1, AA -> 26."""
    n = 0
    for ch in (text or ""):
        if ch.isalpha():
            n = n * 26 + (ord(ch.upper()) - 64)
    return max(0, n - 1)


def _qcolor(rgb):
    r, g, b = rgb
    return QColor(int(round(r * 255)), int(round(g * 255)), int(round(b * 255)))


def _rgb(color):
    return (round(color.redF(), 4), round(color.greenF(), 4), round(color.blueF(), 4))


class ColorSwatchDelegate(QStyledItemDelegate):
    """Рисует цвет как swatch поверх ячейки, не перекрывая выделение строки."""

    def paint(self, painter, option, index):
        super().paint(painter, option, index)
        color = index.data(Qt.UserRole)
        if isinstance(color, QColor) and color.isValid():
            painter.save()
            painter.setRenderHint(QPainter.Antialiasing, True)
            rect = option.rect.adjusted(6, 5, -6, -5)
            painter.setPen(Qt.NoPen)
            painter.setBrush(QBrush(color))
            painter.drawRoundedRect(rect, 3, 3)
            painter.restore()


class StyleEditorDialog(QDialog):
    """Визуальный редактор условного форматирования листа.

    Таблица-превью: ширину столбцов можно менять перетаскиванием границ,
    количество строк — спинбоксом. Правила правятся в таблице снизу.
    """

    def __init__(self, path, opts=None, opts_path=None, parent=None):
        super().__init__(parent)
        self.path = path
        self.opts = opts or {}
        self.opts_path = opts_path
        self.setWindowTitle("Редактор стиля листа")
        self.setModal(True)
        self.setMinimumSize(900, 600)
        self.setObjectName("OptionsDialog")
        opstion_style(self)

        self.font_family = "Courier New"
        self.font_size = 11
        self.col_widths = {}
        self.rules = []
        self.sheet_rows = int(self.opts.get("gs_sheet_rows", 500))
        self.sheet_cols = int(self.opts.get("gs_sheet_cols", 2))
        self.frozen_rows = 1
        self.row_template = self.opts.get("gs_row_template", "") or "{board:<50} |    {serial}    |"
        self.header_template = self.opts.get("gs_header_template", "") or "{board:<50} | Всего: {count} | {serial}"
        self._refreshing = False

        self.load()
        self.build_ui()
        self.refresh_grid()
        self.refresh_rules_table()

    # ------------------------------------------------------------------ #
    # Load / save
    # ------------------------------------------------------------------ #
    def load(self):
        self.rules = []
        self.col_widths = {}
        self.font_family = "Courier New"
        self.font_size = 11
        self.sheet_rows = int(self.opts.get("gs_sheet_rows", 500))
        self.sheet_cols = int(self.opts.get("gs_sheet_cols", 2))
        self.frozen_rows = 1
        if not os.path.exists(self.path):
            return
        with open(self.path, "r", encoding="utf-8") as f:
            data = json.load(f)
        for req in data.get("requests", []):
            self._parse_request(req)

    def _parse_request(self, req):
        if "repeatCell" in req:
            try:
                tf = req["repeatCell"]["cell"]["userEnteredFormat"]["textFormat"]
                self.font_family = tf.get("fontFamily", self.font_family)
                self.font_size = int(tf.get("fontSize", self.font_size))
            except Exception:
                pass
        elif "updateDimensionProperties" in req:
            r = req["updateDimensionProperties"]["range"]
            if r.get("dimension") == "COLUMNS":
                px = req["updateDimensionProperties"]["properties"].get("pixelSize")
                if px is not None:
                    for c in range(int(r.get("startIndex", 0)), int(r.get("endIndex", 0))):
                        self.col_widths[c] = int(px)
        elif "updateSheetProperties" in req:
            gp = req["updateSheetProperties"].get("properties", {}).get("gridProperties", {})
            if "rowCount" in gp:
                self.sheet_rows = int(gp["rowCount"])
            if "columnCount" in gp:
                self.sheet_cols = int(gp["columnCount"])
            if "frozenRowCount" in gp:
                self.frozen_rows = int(gp["frozenRowCount"])
        elif "addConditionalFormatRule" in req:
            rule = req["addConditionalFormatRule"]["rule"]
            br = rule.get("booleanRule", {})
            cond = br.get("condition", {})
            bg = br.get("format", {}).get("backgroundColor", {})
            ctype = cond.get("type")
            values = [v.get("userEnteredValue", "") for v in cond.get("values", [])]
            for rng in rule.get("ranges", []):
                self.rules.append({
                    "type": ctype,
                    "value": values[0] if values else "",
                    "color": (float(bg.get("red", 1)), float(bg.get("green", 1)), float(bg.get("blue", 1))),
                    "start_col": int(rng.get("startColumnIndex", 0)),
                    "end_col": int(rng.get("endColumnIndex", 0)),
                    "start_row": rng.get("startRowIndex"),
                    "end_row": rng.get("endRowIndex"),
                })

    def save(self):
        self.font_family = self.ed_font.text().strip() or "Courier New"
        self.font_size = int(self.sp_font.value())
        self.sheet_rows = int(self.sp_rows.value())
        self.sheet_cols = int(self.sp_cols.value())
        self.frozen_rows = int(self.sp_frozen.value())

        # Строки листа должны вмещать правила
        for rule in self.rules:
            if rule.get("end_row") is not None:
                self.sheet_rows = max(self.sheet_rows, rule["end_row"])
        # Правила и ширины за пределами выбранного числа столбцов отбрасываем
        self.rules = [r for r in self.rules if r["start_col"] < self.sheet_cols]
        for r in self.rules:
            r["end_col"] = min(max(r["start_col"] + 1, r["end_col"]), self.sheet_cols)
        self.col_widths = {c: w for c, w in self.col_widths.items() if c < self.sheet_cols}

        requests = [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": None,
                    "gridProperties": {
                        "rowCount": self.sheet_rows,
                        "columnCount": self.sheet_cols,
                        "frozenRowCount": self.frozen_rows,
                    },
                },
                "fields": "gridProperties.rowCount,gridProperties.columnCount,gridProperties.frozenRowCount",
            }
        }, {
            "repeatCell": {
                "range": {"sheetId": None},
                "cell": {"userEnteredFormat": {"textFormat": {
                    "fontFamily": self.font_family,
                    "fontSize": self.font_size,
                }}},
                "fields": "userEnteredFormat.textFormat.fontFamily,userEnteredFormat.textFormat.fontSize",
            }
        }]

        for i, rule in enumerate(self.rules):
            rng = {
                "sheetId": None,
                "startColumnIndex": rule["start_col"],
                "endColumnIndex": max(rule["start_col"] + 1, rule["end_col"]),
            }
            if rule.get("start_row") is not None:
                rng["startRowIndex"] = rule["start_row"]
            if rule.get("end_row") is not None:
                rng["endRowIndex"] = rule["end_row"]
            cond = {"type": rule["type"]}
            if rule["type"] in ("TEXT_CONTAINS", "TEXT_NOT_CONTAINS"):
                cond["values"] = [{"userEnteredValue": rule["value"]}]
            r, g, b = rule["color"]
            requests.append({
                "addConditionalFormatRule": {
                    "rule": {
                        "ranges": [rng],
                        "booleanRule": {
                            "condition": cond,
                            "format": {"backgroundColor": {"red": r, "green": g, "blue": b}},
                        },
                    },
                    "index": i,
                }
            })

        for c in sorted(self.col_widths):
            requests.append({
                "updateDimensionProperties": {
                    "range": {
                        "sheetId": None,
                        "dimension": "COLUMNS",
                        "startIndex": c,
                        "endIndex": c + 1,
                    },
                    "properties": {"pixelSize": int(self.col_widths[c])},
                    "fields": "pixelSize",
                }
            })

        os.makedirs(os.path.dirname(self.path) or ".", exist_ok=True)
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump({"requests": requests}, f, indent=2, ensure_ascii=False)

    # ------------------------------------------------------------------ #
    # UI
    # ------------------------------------------------------------------ #
    def build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(12, 12, 12, 12)
        layout.setSpacing(8)

        header = QLabel("Визуальный редактор условного форматирования")
        header.setStyleSheet("font-size: 12pt; font-weight: 700; color: #eef5ff;")
        layout.addWidget(header)

        tabs = QTabWidget()
        layout.addWidget(tabs, 1)

        # --- Сетка и ширины ---
        grid_tab = QWidget()
        grid_layout = QVBoxLayout(grid_tab)
        grid_layout.setContentsMargins(0, 0, 0, 0)
        grid_layout.setSpacing(6)

        toolbar = QHBoxLayout()
        toolbar.addWidget(QLabel("Строк:"))
        self.sp_rows = QSpinBox()
        self.sp_rows.setRange(1, 1000)
        self.sp_rows.setValue(self.sheet_rows)
        self.sp_rows.valueChanged.connect(self._on_rows_changed)
        toolbar.addWidget(self.sp_rows)
        toolbar.addWidget(QLabel("Столбцов:"))
        self.sp_cols = QSpinBox()
        self.sp_cols.setRange(1, 64)
        self.sp_cols.setValue(self.sheet_cols)
        self.sp_cols.valueChanged.connect(self._on_cols_changed)
        toolbar.addWidget(self.sp_cols)
        toolbar.addWidget(QLabel("Закрепить строк:"))
        self.sp_frozen = QSpinBox()
        self.sp_frozen.setRange(0, 10)
        self.sp_frozen.setValue(self.frozen_rows)
        toolbar.addWidget(self.sp_frozen)
        toolbar.addSpacing(16)
        toolbar.addWidget(QLabel("Шрифт:"))
        self.ed_font = QLineEdit(self.font_family)
        self.ed_font.setMaximumWidth(150)
        toolbar.addWidget(self.ed_font)
        toolbar.addWidget(QLabel("Размер:"))
        self.sp_font = QSpinBox()
        self.sp_font.setRange(6, 48)
        self.sp_font.setValue(self.font_size)
        toolbar.addWidget(self.sp_font)
        toolbar.addStretch()
        grid_layout.addLayout(toolbar)

        self.grid = QTableWidget()
        self.grid.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.grid.setSelectionMode(QAbstractItemView.NoSelection)
        self.grid.verticalHeader().setVisible(True)
        self.grid.verticalHeader().setDefaultSectionSize(22)
        self.grid.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.grid.horizontalHeader().setMinimumSectionSize(40)
        self.grid.horizontalHeader().sectionResized.connect(self._on_col_resized)
        self.grid.setFont(QFont(self.font_family, self.font_size))
        grid_layout.addWidget(self.grid, 1)

        hint = QLabel("Ширину столбцов меняйте перетаскиванием границ заголовков. Цвет ячеек — предпросмотр правил.")
        hint.setStyleSheet("color: #9aa7b8;")
        hint.setWordWrap(True)
        grid_layout.addWidget(hint)
        tabs.addTab(grid_tab, "Сетка и ширины")

        # --- Правила ---
        rules_tab = QWidget()
        rules_layout = QVBoxLayout(rules_tab)
        rules_layout.setContentsMargins(0, 0, 0, 0)
        rules_layout.setSpacing(6)

        rules_label = QLabel("Правила (первое совпадение сверху вниз):")
        rules_label.setStyleSheet("font-weight: 600;")
        rules_layout.addWidget(rules_label)

        self.rules_table = QTableWidget(0, 5)
        self.rules_table.setHorizontalHeaderLabels(["Условие", "Значение", "Фон", "Столбцы", "Строки"])
        self.rules_table.horizontalHeader().setSectionResizeMode(QHeaderView.Stretch)
        self.rules_table.verticalHeader().setVisible(False)
        self.rules_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.rules_table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.rules_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.rules_table.cellDoubleClicked.connect(self._on_rule_double_clicked)
        self.rules_table.setItemDelegateForColumn(2, ColorSwatchDelegate(self.rules_table))
        rules_layout.addWidget(self.rules_table, 1)

        rules_btns = QHBoxLayout()
        btn_add = QPushButton()
        btn_add.setIcon(qta.icon("fa5s.plus", color="#11111b"))
        btn_add.setToolTip("Добавить правило")
        btn_add.clicked.connect(self.add_rule)
        btn_del = QPushButton()
        btn_del.setIcon(qta.icon("fa5s.trash-alt", color="#11111b"))
        btn_del.setToolTip("Удалить правило")
        btn_del.clicked.connect(self.delete_rule)
        btn_up = QPushButton()
        btn_up.setIcon(qta.icon("fa5s.arrow-up", color="#11111b"))
        btn_up.setToolTip("Переместить правило вверх")
        btn_up.clicked.connect(lambda: self.move_rule(-1))
        btn_down = QPushButton()
        btn_down.setIcon(qta.icon("fa5s.arrow-down", color="#11111b"))
        btn_down.setToolTip("Переместить правило вниз")
        btn_down.clicked.connect(lambda: self.move_rule(1))
        for b in (btn_add, btn_del, btn_up, btn_down):
            rules_btns.addWidget(b)
        rules_btns.addStretch()
        rules_layout.addLayout(rules_btns)
        tabs.addTab(rules_tab, "Правила")

        # --- Шаблоны ---
        tmpl_tab = QWidget()
        tmpl_layout = QVBoxLayout(tmpl_tab)
        tmpl_layout.setContentsMargins(0, 0, 0, 0)
        tmpl_layout.setSpacing(6)

        tmpl_label = QLabel("Шаблоны содержимого ячеек:")
        tmpl_label.setStyleSheet("font-weight: 600;")
        tmpl_layout.addWidget(tmpl_label)

        row_row = QHBoxLayout()
        row_row.addWidget(QLabel("Строка данных:"))
        self.ed_row_template = QLineEdit(self.row_template)
        self.ed_row_template.textChanged.connect(self._update_preview)
        row_row.addWidget(self.ed_row_template, 1)
        tmpl_layout.addLayout(row_row)

        header_row = QHBoxLayout()
        header_row.addWidget(QLabel("Заголовок:"))
        self.ed_header_template = QLineEdit(self.header_template)
        self.ed_header_template.textChanged.connect(self._update_preview)
        header_row.addWidget(self.ed_header_template, 1)
        tmpl_layout.addLayout(header_row)

        tmpl_hint = QLabel(
            "Плейсхолдеры: {board} — номер платы, {serial} — серийный номер, {count} — счётчик.\n"
            "Ширина поля: {board:<50} (выравнивание по левому краю), {count:>50} — по правому, {serial:^30} — по центру.\n"
            "Спейсер (пустое поле): {:<30} — 30 пробелов; можно вставлять между плейсхолдерами и вокруг текста.\n"
            "«Проставить ширины» добавит недостающим плейсхолдерам {name:<ширина}; готовые ширины и спейсеры не трогает."
        )
        tmpl_hint.setStyleSheet("color: #9aa7b8;")
        tmpl_hint.setWordWrap(True)
        tmpl_layout.addWidget(tmpl_hint)

        width_row = QHBoxLayout()
        btn_auto_width = QPushButton()
        btn_auto_width.setIcon(qta.icon("fa5s.ruler-combined", color="#11111b"))
        btn_auto_width.setToolTip("Автоширина столбца A по шаблону")
        btn_auto_width.clicked.connect(self._auto_width)
        width_row.addWidget(btn_auto_width)
        self.lbl_width = QLabel("")
        self.lbl_width.setStyleSheet("color: #9aa7b8;")
        width_row.addWidget(self.lbl_width)
        width_row.addStretch()
        tmpl_layout.addLayout(width_row)

        gap_row = QHBoxLayout()
        gap_row.addWidget(QLabel("Отступ:"))
        self.sp_gap = QSpinBox()
        self.sp_gap.setRange(0, 50)
        self.sp_gap.setValue(10)
        gap_row.addWidget(self.sp_gap)
        btn_apply_gaps = QPushButton()
        btn_apply_gaps.setIcon(qta.icon("fa5s.align-left", color="#11111b"))
        btn_apply_gaps.setToolTip("Проставить ширины: добавить {name:<ширина} только к плейсхолдерам без ширины; готовые ширины и спейсеры не меняются")
        btn_apply_gaps.clicked.connect(self._apply_gaps)
        gap_row.addWidget(btn_apply_gaps)
        gap_row.addStretch()
        tmpl_layout.addLayout(gap_row)

        self.lbl_preview = QLabel()
        self.lbl_preview.setWordWrap(True)
        self.lbl_preview.setStyleSheet(
            "background: #0f141c; border: 1px solid #30445f; border-radius: 6px;"
            " padding: 6px; color: #cfe3ff; font-family: monospace;"
        )
        self.lbl_preview.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.lbl_preview.setAlignment(Qt.AlignTop)
        tmpl_layout.addWidget(self.lbl_preview)
        tmpl_layout.addStretch()
        tabs.addTab(tmpl_tab, "Шаблоны")
        self._update_preview()

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.button(QDialogButtonBox.Save).setText("Сохранить")
        buttons.button(QDialogButtonBox.Cancel).setText("Отмена")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    # ------------------------------------------------------------------ #
    # Grid preview
    # ------------------------------------------------------------------ #
    def _n_columns(self):
        if hasattr(self, "sp_cols"):
            return max(1, self.sp_cols.value())
        return max(1, self.sheet_cols)

    def _on_rows_changed(self, _value):
        self.sheet_rows = self.sp_rows.value()
        self.refresh_grid()

    def _on_cols_changed(self, _value):
        self.sheet_cols = self.sp_cols.value()
        self.refresh_grid()

    def _on_col_resized(self, index, _old, new):
        if self._refreshing:
            return
        self.col_widths[index] = int(new)

    def refresh_grid(self):
        self._refreshing = True
        try:
            n_cols = self._n_columns()
            n_rows = self.sp_rows.value()
            self.grid.setColumnCount(n_cols)
            self.grid.setRowCount(n_rows)
            for c in range(n_cols):
                self.grid.setHorizontalHeaderItem(c, QTableWidgetItem(_col_letter(c)))
                self.grid.setColumnWidth(c, int(self.col_widths.get(c, DEFAULT_WIDTH)))
            for r in range(n_rows):
                self.grid.setVerticalHeaderItem(r, QTableWidgetItem(str(r + 1)))
            self.grid.setFont(QFont(self.font_family, self.font_size))
            for r in range(n_rows):
                for c in range(n_cols):
                    item = QTableWidgetItem(self._sample_text(r, c))
                    item.setForeground(QBrush(QColor("#101010")))
                    item.setBackground(QBrush(self._cell_color(r, c)))
                    self.grid.setItem(r, c, item)
            if hasattr(self, "lbl_width"):
                self.lbl_width.setText(f"Ширина A: {self.col_widths.get(0, DEFAULT_WIDTH)} px")
        finally:
            self._refreshing = False

    def _sample_text(self, r, c):
        if r == 0:
            return ["Номер платы", "Серийный номер"][c] if c < 2 else ""
        if c == 0:
            return "CUINF7 0001"
        if c == 1:
            return "Q_ITL580_v1.7"
        return ""

    def _cell_color(self, r, c):
        text = self._sample_text(r, c)
        for rule in self.rules:
            if not (rule["start_col"] <= c < rule["end_col"]):
                continue
            if rule.get("start_row") is not None and r < rule["start_row"]:
                continue
            if rule.get("end_row") is not None and r >= rule["end_row"]:
                continue
            if self._matches(rule, text):
                return _qcolor(rule["color"])
        return QColor("#ffffff")

    @staticmethod
    def _matches(rule, text):
        t = rule["type"]
        if t == "BLANK":
            return text == ""
        if t == "NOT_BLANK":
            return text != ""
        if t == "TEXT_CONTAINS":
            return rule["value"] in text
        if t == "TEXT_NOT_CONTAINS":
            return rule["value"] not in text
        return False

    # ------------------------------------------------------------------ #
    # Rules table
    # ------------------------------------------------------------------ #
    def refresh_rules_table(self):
        self.rules_table.setRowCount(len(self.rules))
        for i, rule in enumerate(self.rules):
            self.rules_table.setItem(i, 0, QTableWidgetItem(rule["type"]))
            self.rules_table.setItem(i, 1, QTableWidgetItem(rule["value"]))
            color_item = QTableWidgetItem("")
            color_item.setData(Qt.UserRole, _qcolor(rule["color"]))
            self.rules_table.setItem(i, 2, color_item)
            self.rules_table.setItem(i, 3, QTableWidgetItem(self._cols_to_text(rule)))
            self.rules_table.setItem(i, 4, QTableWidgetItem(self._rows_to_text(rule)))
        self.refresh_grid()

    def _on_rule_double_clicked(self, row, col):
        if not (0 <= row < len(self.rules)):
            return
        rule = self.rules[row]
        if col == 0:
            idx = COND_TYPES.index(rule["type"]) if rule["type"] in COND_TYPES else 0
            choice, ok = QInputDialog.getItem(self, "Условие", "Тип условия:", COND_TYPES, idx, False)
            if ok:
                rule["type"] = choice
        elif col == 1:
            text, ok = QInputDialog.getText(self, "Значение", "Значение (для TEXT_CONTAINS / TEXT_NOT_CONTAINS):",
                                            text=rule["value"])
            if ok:
                rule["value"] = text.strip()
        elif col == 2:
            color = QColorDialog.getColor(_qcolor(rule["color"]), self, "Цвет фона")
            if color.isValid():
                rule["color"] = _rgb(color)
        elif col == 3:
            text, ok = QInputDialog.getText(self, "Столбцы", "Столбцы (A или A:B):",
                                            text=self._cols_to_text(rule))
            if ok:
                self._parse_cols(rule, text)
        elif col == 4:
            text, ok = QInputDialog.getText(self, "Строки", "Строки (все, 1, 1-10):",
                                            text=self._rows_to_text(rule))
            if ok:
                self._parse_rows(rule, text)
        self.refresh_rules_table()

    def add_rule(self):
        self.rules.append({
            "type": "TEXT_CONTAINS",
            "value": "CUINF7",
            "color": (0.3529, 0.8392, 0.3529),
            "start_col": 0,
            "end_col": 1,
            "start_row": None,
            "end_row": None,
        })
        self.refresh_rules_table()
        self.rules_table.selectRow(len(self.rules) - 1)

    def delete_rule(self):
        row = self.rules_table.currentRow()
        if 0 <= row < len(self.rules):
            self.rules.pop(row)
            self.refresh_rules_table()

    def move_rule(self, delta):
        row = self.rules_table.currentRow()
        new = row + delta
        if 0 <= row < len(self.rules) and 0 <= new < len(self.rules):
            self.rules[row], self.rules[new] = self.rules[new], self.rules[row]
            self.refresh_rules_table()
            self.rules_table.selectRow(new)

    @staticmethod
    def _cols_to_text(rule):
        a = _col_letter(rule["start_col"])
        b = _col_letter(max(rule["start_col"], rule["end_col"] - 1))
        return a if a == b else f"{a}:{b}"

    @staticmethod
    def _rows_to_text(rule):
        s, e = rule.get("start_row"), rule.get("end_row")
        if s is None and e is None:
            return "все"
        if s is not None and e is not None:
            return f"{s + 1}-{e}"
        if s is not None:
            return f"{s + 1}+"
        return f"1-{e}"

    def _parse_cols(self, rule, text):
        text = (text or "").strip().upper().replace(" ", "")
        if ":" in text:
            a, b = text.split(":", 1)
        else:
            a = b = text
        rule["start_col"] = _letter_to_idx(a)
        rule["end_col"] = _letter_to_idx(b) + 1
        if rule["end_col"] <= rule["start_col"]:
            rule["end_col"] = rule["start_col"] + 1

    def _parse_rows(self, rule, text):
        text = (text or "").strip().lower().replace(" ", "")
        if text in ("все", "all", ""):
            rule["start_row"] = None
            rule["end_row"] = None
            return
        try:
            if "-" in text:
                a, b = text.split("-", 1)
                rule["start_row"] = max(0, int(a) - 1)
                rule["end_row"] = int(b) if b not in ("+", "") else None
            elif text.endswith("+"):
                rule["start_row"] = max(0, int(text[:-1]) - 1)
                rule["end_row"] = None
            else:
                rule["start_row"] = max(0, int(text) - 1)
                rule["end_row"] = None
        except ValueError:
            pass

    def _render_template(self, tmpl, values):
        return safe_format(tmpl, **values)

    def _sample_board(self):
        prefix = (self.opts.get("board_prefix", "") or "").strip() or "Board_"
        sep = "" if prefix.endswith(("_", "-")) else "_"
        return f"{prefix}{sep}20261113_105121"

    def _auto_width(self):
        max_px, required = self._required_width()
        self.col_widths[0] = required
        self.refresh_grid()
        if hasattr(self, "lbl_width"):
            self.lbl_width.setText(f"Ширина A по шаблону: {required} px (текст {max_px} px)")

    def _update_preview(self):
        if not hasattr(self, "lbl_preview"):
            return
        row = self._render_template(
            self.ed_row_template.text(),
            {"board": "Q_ITL580_v1.7_C14_20261113_105121", "serial": "CUINF7010092", "count": "42"},
        )
        header = self._render_template(
            self.ed_header_template.text(),
            {"board": "Номер платы", "serial": "Серийный номер", "count": "42"},
        )
        self.lbl_preview.setText(f"Заголовок: {header}\nСтрока:    {row}")
        self._ensure_min_width()

    def _apply_gaps(self):
        gap = max(0, int(self.sp_gap.value()))
        samples = {
            "board": [
                self._sample_board(),
                "Q_ITL580_v1.7_C14_20261113_105121",
                "Номер платы",
            ],
            "serial": ["CUINF7010092", "Серийный номер", "0000000000"],
            "count": ["999999", "42", "0"],
        }
        widths = {name: max(len(s) for s in vals) + gap for name, vals in samples.items()}

        ph_re = re.compile(r"\{(" + "|".join(samples) + r")\}")
        def repl(m):
            return "{" + m.group(1) + ":<" + str(widths[m.group(1)]) + "}"

        for ed in (self.ed_row_template, self.ed_header_template):
            ed.setText(ph_re.sub(repl, ed.text()))
        self._update_preview()

    def _required_width(self):
        fm = QFontMetrics(QFont(self.font_family, self.font_size))
        board = self._sample_board()
        samples = [
            self._render_template(self.ed_header_template.text(),
                                  {"board": "Номер платы", "serial": "Серийный номер", "count": "999999"}),
            self._render_template(self.ed_row_template.text(),
                                  {"board": board, "serial": "CUINF7010092", "count": "999999"}),
        ]
        max_px = max((fm.horizontalAdvance(s) for s in samples if s), default=0)
        return max_px, max(80, max_px + 24)

    def _ensure_min_width(self):
        """Расширяем столбец A, если шаблон стал длиннее (только рост)."""
        if not hasattr(self, "grid"):
            return
        max_px, required = self._required_width()
        if self.col_widths.get(0, 0) < required:
            self.col_widths[0] = required
            self.grid.setColumnWidth(0, required)
            if hasattr(self, "lbl_width"):
                self.lbl_width.setText(f"Ширина A по шаблону: {required} px (текст {max_px} px)")

    def _save_templates(self):
        if not self.opts_path:
            return
        data = {}
        if os.path.exists(self.opts_path):
            try:
                with open(self.opts_path, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                data = {}
        data["gs_row_template"] = self.ed_row_template.text()
        data["gs_header_template"] = self.ed_header_template.text()
        os.makedirs(os.path.dirname(self.opts_path) or ".", exist_ok=True)
        with open(self.opts_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=4, ensure_ascii=False)

    def _on_save(self):
        try:
            self.save()
            self._save_templates()
        except Exception as e:
            QMessageBox.critical(self, "Ошибка", str(e))
            return
        self.accept()