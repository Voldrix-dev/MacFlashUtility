from PySide6.QtWidgets import QWidget, QLineEdit, QLabel, QDialog

def apply_dark_style(widget: QWidget):
    widget.setStyleSheet("""
        QWidget {
            background-color: #12151c;
            color: #e7ecf3;
            font-family: 'Noto Sans', 'DejaVu Sans', 'Ubuntu', sans-serif;
            font-size: 10pt;
        }

        #HeroCard {
            border-radius: 12px;
            border: 1px solid #2f4a70;
            background: qlineargradient(
                x1:0, y1:0, x2:1, y2:1,
                stop:0 #1f2f46,
                stop:0.55 #1c2a3d,
                stop:1 #15273f
            );
        }

        #HeroTitle {
            color: #eef5ff;
            font-size: 15pt;
            font-weight: 700;
            letter-spacing: 0.5px;
        }

        #WorkflowLabel {
            color: #9bc7ff;
            font-size: 10pt;
            font-weight: 600;
            background-color: rgba(10, 18, 30, 0.55);
            border: 1px solid rgba(120, 170, 230, 0.35);
            border-radius: 8px;
            padding: 6px 10px;
        }
                         
        QToolTip {
            background-color: #202736;
            color: #f0f0f0;
            border: 1px solid #3c84d8;
            border-radius: 6px;
            padding: 6px 8px;
            font-family: 'Noto Sans', 'Ubuntu', sans-serif;
            font-size: 9pt;
        }

        QMenuBar {
            background-color: #1b2230;
            color: #d6deea;
            border-bottom: 1px solid #2d3748;
        }
        QMenuBar::item:selected {
            background-color: #2a3a52;
            border-radius: 4px;
        }

        QMenu {
            background-color: #1b2230;
            color: #d6deea;
            border: 1px solid #2d3748;
            border-radius: 8px;
        }
        QMenu::item:selected {
            background-color: #2a3a52;
        }

        QLabel {
            color: #ced8e6;
        }

        QLineEdit {
            background-color: #1c2330;
            color: #f0f0f0;
            border: 1px solid #35465e;
            border-radius: 6px;
            padding: 6px 8px;
            selection-background-color: #3c84d8;
        }
        QLineEdit:focus {
            border: 1px solid #58a6ff;
            background-color: #1f2a3b;
        }

        QPushButton {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #27466d, stop:1 #1f3654);
            color: #eff6ff;
            border: 1px solid #40628c;
            border-radius: 7px;
            padding: 5px 10px;
            font-weight: 600;
        }
        QPushButton:hover {
            background: qlineargradient(x1:0,y1:0,x2:0,y2:1,stop:0 #2f5787, stop:1 #26466d);
            border: 1px solid #5c88be;
        }
        QPushButton:pressed {
            background-color: #1a2f49;
            color: #dcecff;
        }

        QTextEdit {
            background-color: #0f141d;
            color: #d7e2ef;
            border: 1px solid #2e3a4d;
            border-radius: 6px;
            padding: 6px;
            font-family: "Fira Code", "JetBrains Mono", "Courier New", monospace;
            font-size: 10pt;
        }

        QScrollBar:vertical {
            border: none;
            background: #111;
            width: 10px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:vertical {
            background: #555;
            border-radius: 5px;
            min-height: 20px;
        }
        QScrollBar::handle:vertical:hover {
            background: #777;
        }
        QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
            height: 0px;
        }

        QScrollBar:horizontal {
            border: none;
            background: #111;
            height: 10px;
            margin: 0px 0px 0px 0px;
        }
        QScrollBar::handle:horizontal {
            background: #555;
            border-radius: 5px;
            min-width: 20px;
        }
        QScrollBar::handle:horizontal:hover {
            background: #777;
        }
        QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
            width: 0px;
        }
                         
        QListWidget {
            background-color: #101722;
            color: #e1e1e1;
            border-radius: 6px;
            padding: 2px;
        }

        QListWidget::item {
            padding: 6px 10px;
            border-radius: 4px;
        }

        QListWidget::item:selected {
            background-color: #3c84d8;
            color: #ffffff;
        }

        QListWidget::item:hover {
            background-color: #243042;
            color: #f0f0f0;
        }
                         
        QComboBox {
            background: #141f2d;
            color: #e6edf3;
            border: 1px solid #2a3a50;
            border-radius: 10px;
            padding: 2px 8px;
        }

        QComboBox:hover {
            border: 1px solid #425a7d;
        }

        QComboBox:focus {
            border: 1px solid #4f79b7;
        }

        QComboBox::drop-down {
            width: 24px;
            border: none;
            background: transparent;
        }

        QComboBox QAbstractItemView {
            background: #141f2d;
            color: #e6edf3;
            border: 1px solid #2a3a50;
            outline: none;
            selection-background-color: #365a8d;
            selection-color: #ffffff;
        }

        QComboBox::down-arrow {
            image: url(Data/arrow-down.svg);
            width: 10px;
            height: 10px;
        }
    """)


def opstion_style(widget: QDialog):
    widget.setStyleSheet("""
                QDialog#OptionsDialog {
                    background-color: #161d29;
                    color: #dbe5f3;
                }
                QGroupBox {
                    border: 1px solid #30445f;
                    border-radius: 8px;
                    margin-top: 10px;
                    padding-top: 10px;
                    font-weight: 700;
                    color: #e7eef9;
                }
                QGroupBox::title {
                    subcontrol-origin: margin;
                    left: 10px;
                    padding: 0 6px;
                    color: #9fc6ff;
                }
                QLabel {
                    color: #d5e0ef;
                }
                QLineEdit {
                    background-color: #1d2838;
                    color: #f0f6ff;
                    border: 1px solid #3b4f6b;
                    border-radius: 6px;
                    padding: 5px 8px;
                    min-height: 24px;
                }
                QLineEdit:focus {
                    border: 1px solid #59a4ff;
                }
                QPushButton#ModeBtn {
                    background-color: #1d2838;
                    color: #cfe1f8;
                    border: 1px solid #3b4f6b;
                    border-radius: 7px;
                    padding: 6px 10px;
                    font-weight: 600;
                    min-height: 26px;
                }
                QPushButton#ModeBtn:hover {
                    background-color: #25374d;
                    border-color: #5f7ea5;
                }
                QPushButton#ModeBtn:checked {
                    background-color: #2f5787;
                    color: #ffffff;
                    border: 1px solid #6fa8ff;
                }
                QDialogButtonBox QPushButton {
                    min-width: 92px;
                    min-height: 28px;
                }
            """)


class St:
    @staticmethod
    def status(label: QLabel, connected: bool):
        """Обновляет цвет и текст статуса устройства."""
        color = "#66cc66" if connected else "#cc3333"
        label.setStyleSheet(f"""
            color: {color};
            font-weight: bold;
            font-size: 14px;
        """)

    @staticmethod
    def status_level(label: QLabel, level: str):
        """Трёхсостоянийный статус: red | yellow | green."""
        mapping = {
            "red": "#cc3333",
            "yellow": "#ffcc33",
            "green": "#66cc66",
        }
        color = mapping.get(level, "#cc3333")
        label.setStyleSheet(f"""
            color: {color};
            font-weight: bold;
            font-size: 14px;
        """)

    @staticmethod
    def finish (label: QLabel, connected: bool):
        """Обновляет цвет и текст статуса устройства."""
        color = "#66cc66" if connected else "#cc3333"
        label.setStyleSheet(f"""
            color: {color};
            font-weight: bold;
            font-size: 14px;
        """)

    @staticmethod
    def _style_field(field: QLineEdit):
        """Минимальная настройка поля, если нужно вручную."""
        field.setFixedHeight(30)

    @staticmethod
    def sn_info_collor(label: QLabel, send: bool):
        color = "#66cc66" if send else "#cc3333"
        label.setStyleSheet(f"""
            color: {color};
            font-weight: bold;
            font-size: 14px;
        """)
