import os
from pathlib import Path
import sys
import logging
import time
import threading
import subprocess
import json
import ast
import urllib.parse
import re
import markdown
from collections import deque
# Internal
from guiWorkers import SearchWorker, StatsWorker, ModelToggleWorker, DatabaseActionWorker, LLMWorker, SearchFacts
from Parsers import get_drive_service
from main import backend_setup, CONFIG_DATA
# Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QSystemTrayIcon, QMenu, QHeaderView,
    QLabel, QFrame, QAbstractItemView, QTabWidget, QStatusBar, QLineEdit, QScrollArea, QDialog, QTextBrowser, QFileDialog, QCheckBox
)
from PySide6.QtCore import Qt, QSize, Signal, Slot, QEvent
from PySide6.QtGui import QIcon, QPixmap, QFont, QColor, QBrush, QAction, QImage, QDesktopServices
import qtawesome as qta

logger = logging.getLogger("GUI")

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv('LOCALAPPDATA')) / "2nd Brain"

# --- VISUAL CONSTANTS ---
# ACCENT_COLOR = "#61afef"   # Blue
ACCENT_COLOR_2 = "#17616d"   # Second Brain blue
ACCENT_COLOR = "#cbe3a7"   # Second Brain green
BG_DARK      = "#1e2227"   # Main Background
BG_LIGHT     = "#282c34"   # Sidebar/Header
BG_MEDIUM    = "#23272d"
BG_INPUT     = "#181a1f"
TEXT_MAIN    = "#abb2bf"
OUTLINE      = "#3e4451"

def get_tint(color, alpha=0.1):
    """Returns a hex color string with the given alpha applied as a tint."""
    c = QColor(color)
    c.setAlphaF(alpha)
    return c.name(QColor.NameFormat.HexArgb)

# --- LOGGING HANDLER ---
class GuiLogHandler(logging.Handler):
    """A custom logging handler that calls a function with the log record, and sends them to a page in the sidebar."""
    def __init__(self, log_display_callback):
        super().__init__()
        self.log_display_callback = log_display_callback # This will be MainWindow.display_log_message
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s', datefmt='%H:%M:%S')
        self.setFormatter(formatter)
        
    def emit(self, record):
        msg = self.format(record)
        # Call the MainWindow method, which uses the signal for thread safety
        self.log_display_callback(msg)

class FileLinkBrowser(QTextBrowser):
    """Enables the QTextBrowser to open local files when links are clicked by defining a custom handler for anchorClicked."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setOpenExternalLinks(False)
        self.setOpenLinks(False)
        self.anchorClicked.connect(self.handle_link)

    def handle_link(self, url):
        # 1. Get the path (Qt now handles the "file:///" parsing automatically)
        if url.isLocalFile():
            path = url.toLocalFile() # Returns e.g. "Z:/My Drive/File.txt"
            # 2. Normalize for Windows (Flip / back to \)
            path = os.path.normpath(path) 
            logger.info(f"Opening local file: {path}")
            if os.path.exists(path):
                try:
                    os.startfile(path)
                except Exception as e:
                    logger.error(f"Failed to open file: {e}")
            else:
                logger.warning(f"File not found: {path}")        
        else:
            # Handle actual web links (http://google.com)
            QDesktopServices.openUrl(url)

class AdvancedSearchDialog(QDialog):
    """
    A dialog for configuring search filters like specific folders and source types.
    """
    def __init__(self, current_folder, current_sources, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search Filters")
        self.setFixedSize(500, 190)
        
        # Store initial state
        self.folder_path = current_folder
        # Default to all True if None provided
        self.source_filter = current_sources
        
        # Styling
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; }}
            QLabel {{ font-size: 14px; font-weight: bold; color: {TEXT_MAIN}; }}
            QLineEdit {{ 
                background-color: {BG_INPUT}; 
                border: 1px solid {OUTLINE}; 
                border-radius: 0px; 
                padding: 6px; 
                color: white; 
            }}
            QPushButton {{ 
                background-color: {BG_LIGHT}; 
                border: 0px solid {OUTLINE}; 
                border-radius: 0px; 
                padding: 6px 12px; 
                color: white;
            }}
            QPushButton:hover {{ background-color: {OUTLINE}; }}
            QCheckBox {{
                color: {TEXT_MAIN};
                font-size: 13px;
                spacing: 8px;
            }}
            QCheckBox::indicator {{
                width: 18px;
                height: 18px;
                border: 1px solid {OUTLINE};
                background-color: {BG_INPUT};
                border-radius: 2px;
            }}
            QCheckBox::indicator:checked {{
                background-color: {get_tint(ACCENT_COLOR, 0.4)};
                border: 1px solid {ACCENT_COLOR};
            }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- SECTION 1: FOLDER FILTER ---
        layout.addWidget(QLabel("Search in a specific folder:"))
        
        folder_layout = QHBoxLayout()
        folder_layout.setSpacing(0)
        self.txt_folder = QLineEdit(self.folder_path if self.folder_path else "")
        self.txt_folder.setReadOnly(True)
        self.txt_folder.setPlaceholderText("All Folders (Default)")
        self.txt_folder.setStyleSheet("border: none;")
        self.txt_folder.setFixedHeight(33)
        
        self.btn_folder_action = QPushButton()
        self.btn_folder_action.setFixedWidth(60)
        self.btn_folder_action.setFixedHeight(33)
        self.btn_folder_action.clicked.connect(self.handle_folder_toggle)
        self.btn_folder_action.setCursor(Qt.PointingHandCursor)
        
        folder_layout.addWidget(self.txt_folder)
        folder_layout.addWidget(self.btn_folder_action)
        layout.addLayout(folder_layout)

        layout.addStretch()

        # --- SECTION 2: SOURCE FILTER ---
        layout.addWidget(QLabel("Search based on specific sources:"))
        source_layout = QHBoxLayout()
        source_layout.setSpacing(15)

        self.chk_ocr = QCheckBox("OCR")
        self.chk_ocr.setChecked(self.source_filter.get("OCR", True))
        self.chk_ocr.setCursor(Qt.PointingHandCursor)
        
        self.chk_embed = QCheckBox("EMBED")
        self.chk_embed.setChecked(self.source_filter.get("EMBED", True))
        self.chk_embed.setCursor(Qt.PointingHandCursor)
        
        self.chk_llm = QCheckBox("LLM")
        self.chk_llm.setChecked(self.source_filter.get("LLM", True))
        self.chk_llm.setCursor(Qt.PointingHandCursor)
        
        btn_apply = QPushButton("Done")
        btn_apply.setFixedWidth(60)
        btn_apply.setFixedHeight(33)
        hover_tint = get_tint(ACCENT_COLOR, 0.1)
        btn_apply.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {ACCENT_COLOR}; text-align: center; padding: 4px; border-radius: 0px; border: 1px solid {hover_tint}; }}
            QPushButton:hover {{ text-decoration: underline; background-color: {hover_tint}; }}
        """)
        btn_apply.clicked.connect(self.apply_filters)
        btn_apply.setCursor(Qt.PointingHandCursor)

        source_layout.addWidget(self.chk_ocr)
        source_layout.addWidget(self.chk_embed)
        source_layout.addWidget(self.chk_llm)
        source_layout.addStretch()
        source_layout.addWidget(btn_apply)
        layout.addLayout(source_layout)

        # --- ACTION BUTTONS ---
        # btn_layout = QHBoxLayout()
        
        # btn_layout.addStretch()
        # btn_layout.addWidget(btn_apply)
        # layout.addLayout(btn_layout)

        self.update_folder_button()

    def handle_folder_toggle(self):
        if self.txt_folder.text():
            self.txt_folder.clear()
        else:
            folder = QFileDialog.getExistingDirectory(self, "Select Folder to Search In")
            if folder:
                self.txt_folder.setText(os.path.normpath(folder))
        self.update_folder_button()

    def update_folder_button(self):
        if self.txt_folder.text():
            self.btn_folder_action.setIcon(qta.icon('mdi.close'))
            self.btn_folder_action.setToolTip("Clear Selection")
        else:
            self.btn_folder_action.setIcon(qta.icon('mdi.folder-open'))
            self.btn_folder_action.setToolTip("Browse...")

    def apply_filters(self):
        self.folder_path = self.txt_folder.text().strip() or None
        # Capture checkbox states
        self.source_filter = {
            "OCR": self.chk_ocr.isChecked(),
            "EMBED": self.chk_embed.isChecked(),
            "LLM": self.chk_llm.isChecked()
        }
        self.accept()

class ResultDetailsDialog(QDialog):
    """Sprawling class that simply displays a small window when a result is clicked with a few options and facts."""
    def __init__(self, item_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Result Details")
        self.setFixedSize(500, 375)
        self.path = item_data.get('path', 'Unknown')
        
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; }}
            QLabel {{ color: {TEXT_MAIN}; font-size: 18px; }}
            QTextBrowser {{ background-color: {BG_LIGHT}; border: none; padding: 10px; color: {TEXT_MAIN}; font-size: 13px; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(5)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. Header (Filename)
        lbl_name = QLabel(Path(self.path).name)
        lbl_name.setFont(QFont("Segoe UI", 14, QFont.Bold))
        lbl_name.setStyleSheet(f"color: {TEXT_MAIN};")
        layout.addWidget(lbl_name)

        # 2. File Path
        path = item_data.get('path', 'Unknown Path')
        lbl_path = QLabel(f"<I>{path}</I>")
        layout.addWidget(lbl_path)
        lbl_path.setStyleSheet("color: #888; font-size: 11px; ")

        # 3. Metadata Row (Score | Method  | Source | Hits)
        meta_layout = QHBoxLayout()
        score = item_data.get('score', 0.0) * 100
        m_type = item_data.get('result_type', 'Unknown').upper()
        num_hits = item_data.get('num_hits', 1)
        
        lbl_meta = QLabel(f"<b>SCORE:</b> {score:.2f}   |   <b>METHOD:</b> {m_type}   |   <B>SOURCE:</b> {item_data.get('source', 'Unknown')}  |   <b>HITS:</b> {num_hits}")
        lbl_meta.setStyleSheet("color: #888; font-size: 13px; ")
        meta_layout.addWidget(lbl_meta)
        meta_layout.addStretch()
        layout.addLayout(meta_layout)

        layout.addSpacing(10)

        # 4. Content Area (The Text)
        self.text_browser = QTextBrowser()
        content = item_data.get('content', 'No preview text available.').strip()
        if self.path and content.startswith(self.path):
            content = content[len(self.path):].strip()
        self.text_browser.setText(content)
        layout.addWidget(self.text_browser)

        layout.addSpacing(10)

        # 5. Buttons (Open File | Close)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        hover_tint = get_tint(ACCENT_COLOR, 0.1)
        btn_style = f"""
            QPushButton {{ background-color: transparent; color: {ACCENT_COLOR}; text-align: center; padding: 6px; border-radius: 0px; border: 1px solid {hover_tint}; }}
            QPushButton:hover {{ text-decoration: underline; background-color: {hover_tint}; }}
        """

        # Attach File Button
        btn_attach_result = QPushButton("Attach")
        btn_attach_result.setCursor(Qt.PointingHandCursor)
        btn_attach_result.clicked.connect(self.attach_and_close)
        btn_attach_result.setStyleSheet(btn_style)
        
        # Copy Path Button
        btn_copy = QPushButton("Copy Path")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.path))
        btn_copy.setStyleSheet(btn_style)

        # Reveal in Explorer Button
        btn_reveal = QPushButton("Show Location")
        btn_reveal.setCursor(Qt.PointingHandCursor)
        btn_reveal.clicked.connect(self.reveal_in_explorer)
        btn_reveal.setStyleSheet(btn_style)

        # Open File
        btn_open = QPushButton("Open")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(self.open_file)
        btn_open.setStyleSheet(btn_style)
        
        # Close
        btn_close = QPushButton("Close")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        hover_tint = get_tint(TEXT_MAIN, 0.1)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {TEXT_MAIN}; text-align: center; padding: 6px; border-radius: 0px; border: 1px solid {hover_tint}; }}
            QPushButton:hover {{ text-decoration: underline; background-color: {hover_tint}; }}
        """)
        
        # Add to layout
        btn_layout.addWidget(btn_attach_result)
        btn_layout.addWidget(btn_copy)
        btn_layout.addWidget(btn_reveal)
        btn_layout.addWidget(btn_open)
        btn_layout.addStretch() # Spacer
        btn_layout.addWidget(btn_close)
        layout.addLayout(btn_layout)

    def open_file(self):
        try:
            os.startfile(self.path)
        except Exception as e:
            logger.error(f"Failed to open file: {e}")

    def reveal_in_explorer(self):
        """Opens Windows Explorer with the file selected"""
        import subprocess
        try:
            # The '/select,' argument tells Explorer to highlight the file rather than open it
            subprocess.run(['explorer', '/select,', str(Path(self.path).resolve())])
        except Exception as e:
            print(f"Error revealing file: {e}")

    def attach_and_close(self):
        # Replaces the current attachment and closes the dialog
        self.parent().set_attachment(self.path)
        self.accept()

class MainWindow(QMainWindow):
    """The main application window for Second Brain, which includes the system tray window."""
    # Must define the log_signal in order for the log info page to update from other threads
    log_signal = Signal(str)

    def __init__(self):
        super().__init__()
        # Critical structures:
        self.search_engine = None
        self.orchestrator = None
        self.models = None
        self.config = None

        self.workers = []
        self.attached_file_path = None
        self.folder_filter = None  # None means "Search Everything"
        self.source_filter = {"OCR": True, "EMBED": True, "LLM": True}
        
        self.setWindowTitle("Second Brain")
        self.resize(900, 600)
        self.icon_path = str(BASE_DIR / "icon.ico")
        self.setWindowIcon(QIcon(self.icon_path))
        
        self.setup_styles()
        self.setup_ui()
        self.setup_tray()

        # Secret easter egg: Konami Code, might remove later
        self.konami_code = [
            Qt.Key_Up.value, Qt.Key_Up.value, Qt.Key_Down.value, Qt.Key_Down.value,
            Qt.Key_Left.value, Qt.Key_Right.value, Qt.Key_Left.value, Qt.Key_Right.value,
            Qt.Key_B.value, Qt.Key_A.value
        ]
        self.key_history = deque(maxlen=len(self.konami_code))
        self.last_key_time = 0  # Cooldown tracking
        QApplication.instance().installEventFilter(self)

        # Configure Logging for log info page
        self.log_signal.connect(self.display_log_message_safe)
        self.log_handler = GuiLogHandler(self.log_signal.emit)
        # Get the root logger and add the custom handler, also for the log info page
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_handler)
        root_logger.setLevel(logging.INFO)

    def setup_styles(self):
        """Many key elements of the application are styled here, but not all."""
        self.setStyleSheet(f"""
            QMainWindow {{ background-color: {BG_DARK}; }}
            QWidget {{ color: {TEXT_MAIN}; font-family: 'Segoe UI', sans-serif; font-size: 14px; }}
            QFrame#Sidebar {{ background-color: {BG_DARK}; border-right: 1px solid {OUTLINE}; }}
            QPushButton {{ border: none; background: transparent; border-radius: 0px; }}
            QPushButton:hover {{ background-color: {OUTLINE}; }}
            QPushButton:checked {{ background-color: {OUTLINE}; border-left: 3px solid {ACCENT_COLOR}; }}
            QTextEdit {{
                background-color: {BG_INPUT}; border: 0px; border-radius: 6px; padding: 12px; color: white; font-size: 15px;
            }}
            QTextEdit:focus {{ border: 0px; }}
            QTabWidget::pane {{ 
                border-top: 1px solid {OUTLINE}; 
                border-bottom: 0px;
                border-left: 0px; 
                border-right: 0px;
                top: -1px; }}
            QTabBar::tab {{
                background: {BG_DARK}; color: {TEXT_MAIN}; padding: 10px 30px; border-bottom: 1px solid {OUTLINE}; border-top-left-radius: 4px; border-top-right-radius: 4px; margin-right: 2px;
            }}
            QTabBar::tab:selected {{ background: {BG_DARK}; color: {ACCENT_COLOR}; border-bottom: 2px solid {ACCENT_COLOR}; }}
            QTableWidget, QListWidget {{ background-color: {BG_DARK}; border: none; outline: 0; }}
            QTableWidget::item:focus, QListWidget::item:focus {{ border: none; outline: none; }}
            QHeaderView::section {{ background-color: {BG_DARK}; color: {ACCENT_COLOR}; border: none; padding: 5px; }}
            QStatusBar {{ background-color: {BG_LIGHT}; color: #888; }}
        """)

    def setup_ui(self):
        """Sets up the main UI elements of the application."""
        central_widget = QWidget()
        self.setCentralWidget(central_widget)
        main_layout = QHBoxLayout(central_widget)
        main_layout.setContentsMargins(0, 0, 0, 0) # L, T, R, B
        main_layout.setSpacing(0)

    # SIDEBAR
        self.sidebar = QFrame()
        self.sidebar.setObjectName("Sidebar")
        self.sidebar.setFixedWidth(42)
        side_layout = QVBoxLayout(self.sidebar)
        side_layout.setContentsMargins(0, 48, 0, 0) # L, T, R, B
        side_layout.setSpacing(0)

        # SEARCH BUTTON
        search_icon = qta.icon('ri.search-line')
        self.btn_search = QPushButton("")
        self.btn_search.setToolTip("Search")
        self.btn_search.setIcon(search_icon)
        self.btn_search.setCheckable(True)
        self.btn_search.setChecked(True)
        self.btn_search.clicked.connect(lambda: self.switch_page(0))
        self.btn_search.setCursor(Qt.PointingHandCursor)
        self.btn_search.setIconSize(QSize(26, 26))
        self.btn_search.setFixedSize(42, 42)
        
        # SETTINGS BUTTON
        settings_icon = qta.icon('msc.settings-gear')
        self.btn_settings = QPushButton("")
        self.btn_settings.setToolTip("Settings")
        self.btn_settings.setIcon(settings_icon)
        self.btn_settings.setCheckable(True)
        self.btn_settings.clicked.connect(lambda: self.switch_page(1))
        self.btn_settings.setCursor(Qt.PointingHandCursor)
        self.btn_settings.setIconSize(QSize(26, 26))
        self.btn_settings.setFixedSize(42, 42)

        # LOGS BUTTON
        log_icon = qta.icon('mdi6.information-slab-circle', color=ACCENT_COLOR)
        self.btn_logs = QPushButton("")
        self.btn_logs.setToolTip("Logs")
        self.btn_logs.setIcon(log_icon)
        self.btn_logs.setCheckable(True)
        self.btn_logs.clicked.connect(lambda: self.switch_page(2))
        self.btn_logs.setCursor(Qt.PointingHandCursor)
        self.btn_logs.setIconSize(QSize(26, 26))
        self.btn_logs.setFixedSize(42, 42)
        
        # SIDEBAR LAYOUT
        side_layout.addWidget(self.btn_search)
        side_layout.addStretch()
        side_layout.addWidget(self.btn_settings)
        side_layout.addWidget(self.btn_logs)
        main_layout.addWidget(self.sidebar)

        # STACK FOR PAGES
        self.stack = QStackedWidget()
        
    # SEARCH PAGE
        self.page_search = QWidget()
        search_layout = QVBoxLayout(self.page_search)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(15)

        # INPUT WRAPPER - for spacing
        self.input_wrapper = QWidget()
        input_wrapper_layout = QHBoxLayout(self.input_wrapper)
        # Padding for the layout - must be precise
        # This margin applies *around* the input_container_frame inside the wrapper.
        input_wrapper_layout.setContentsMargins(70, 40, 70, 10) # L, T, R, B (set bottom to 0)
        input_wrapper_layout.setSpacing(0)

        # INPUT CONTAINER
        self.input_container_frame = QFrame()
        input_container_layout = QHBoxLayout(self.input_container_frame)
        input_container_layout.setContentsMargins(4, 5, 7, 0) # L, T, R, B - must be precies
        input_container_layout.setSpacing(0)
        self.input_container_frame.setStyleSheet(f"""
            QFrame {{
                color: #e1e2e8;
                background-color: {get_tint(BG_INPUT, 0.1)};
                border: 1px solid #8d9199;
                border-radius: 28px;
                font-size: 14px;
                selection-color: white;
            }}
        """)
        
        # SEARCH BAR - TEXT INPUT
        self.search_input_vertical_padding = 12
        self.search_input = QTextEdit()
        self.search_input.setPlaceholderText("Search")
        # Dynamically adjust height based on content
        doc_height = int(self.search_input.document().size().height())
        self.search_input_min_height = doc_height
        self.search_input_max_height = (doc_height * 7)
        self.search_input.textChanged.connect(self.adjust_search_input_height)
        self.search_input.setFixedHeight(doc_height + (self.search_input_vertical_padding * 2) + 3)  # adjust +3-4 so it doesn't wiggle on first type
        self.search_input.setFixedWidth(self.search_input.width())
        self.search_input.setStyleSheet("border: none;")
        # Custom stylesheet for the QTextEdit inside the input container
        self.search_input.setStyleSheet("""
            QTextEdit {
                color: #e1e2e8;
                background-color: transparent;
                border: none;
                border-radius: 28px;
                font-size: 14px;
                padding: 8px 16px;
                selection-color: white;
            }
        """)
        self.search_input.setCursor(Qt.IBeamCursor)

    # SEARCH PAGE BUTTONS - each of these follow a pattern: icon, tooltip, size, style, padding layout

        # FOLDER FILTER BUTTON
        self.filter_icon = qta.icon('mdi.filter-variant')
        self.btn_filter = QPushButton("")
        self.btn_filter.setToolTip("Searching all files")
        self.btn_filter.setIcon(self.filter_icon)
        self.btn_filter.setIconSize(QSize(26, 26))
        self.btn_filter.setFixedSize(42, 42)
        self.btn_filter.setStyleSheet(f"""
            QPushButton {{
                border-radius: 21px; 
                border: none;
                background-color: transparent;
            }}
            QPushButton:hover {{
                background-color: {BG_LIGHT};
            }}
        """)
        self.btn_filter.clicked.connect(self.handle_filter)
        self.btn_filter.setCursor(Qt.PointingHandCursor)
        self.btn_filter_container = QWidget()
        self.btn_filter_container.setStyleSheet("""background-color: transparent;""")
        btn_filter_layout = QVBoxLayout(self.btn_filter_container)
        # This aligns the icon in the center of the search bar layout:
        btn_filter_layout.setContentsMargins(0, 1.5, 0, 0) # L, T, R, Bottom
        btn_filter_layout.addWidget(self.btn_filter)

        # ATTACH BUTTON
        self.attach_icon = qta.icon('ph.paperclip-bold')
        self.btn_attach = QPushButton("")
        self.btn_attach.setToolTip("Attach")
        self.btn_attach.setIcon(self.attach_icon)
        self.btn_attach.setIconSize(QSize(26, 26))
        self.btn_attach.setFixedSize(42, 42)
        self.btn_attach.setStyleSheet(f"""
            QPushButton {{
                border-radius: 21px; 
                border: none;
                background-color: transparent;
            }}
            QPushButton:hover {{
                background-color: {BG_LIGHT};
            }}
        """)
        self.btn_attach.clicked.connect(self.handle_attach)
        self.btn_attach.setCursor(Qt.PointingHandCursor)
        self.btn_attach_container = QWidget()
        self.btn_attach_container.setStyleSheet("""background-color: transparent;""")
        btn_attach_layout = QVBoxLayout(self.btn_attach_container)
        # This aligns the icon in the center of the search bar layout:
        btn_attach_layout.setContentsMargins(0, 1.5, 0, 0) # L, T, R, Bottom
        btn_attach_layout.addWidget(self.btn_attach)

        # SEND BUTTON
        self.send_icon = qta.icon('mdi.send')
        self.btn_send = QPushButton("")
        self.btn_send.setToolTip("Send")
        self.btn_send.setIcon(self.send_icon)
        spin_animation = qta.Spin(self.btn_send)  # Loading animation
        self.send_spin_icon = qta.icon('fa5s.circle-notch', color='gray', animation=spin_animation)
        self.btn_send.setIconSize(QSize(26, 26))
        self.btn_send.setFixedSize(42, 42)
        self.btn_send.setStyleSheet(f"""
            QPushButton {{
                border-radius: 21px; 
                border: none;
                background-color: transparent;
            }}
            QPushButton:hover {{
                background-color: {BG_LIGHT};
            }}
        """)
        self.btn_send.clicked.connect(self.run_search)
        self.btn_send.setCursor(Qt.PointingHandCursor)
        self.btn_send_container = QWidget()
        self.btn_send_container.setStyleSheet("""background-color: transparent;""")
        btn_send_layout = QVBoxLayout(self.btn_send_container)
        # This aligns the icon in the center of the search bar layout:
        btn_send_layout.setContentsMargins(0, 1.5, 0, 0) # L, T, R, Bottom
        btn_send_layout.addWidget(self.btn_send)

        # ASSEMBLE INPUT CONTAINER
        input_container_layout.addWidget(self.search_input)
        input_container_layout.addWidget(self.btn_filter_container, 0, Qt.AlignmentFlag.AlignTop)
        input_container_layout.addWidget(self.btn_attach_container, 0, Qt.AlignmentFlag.AlignTop)
        input_container_layout.addWidget(self.btn_send_container, 0, Qt.AlignmentFlag.AlignTop)
        # Wrapper for centering
        input_wrapper_layout.addStretch()
        input_wrapper_layout.addWidget(self.input_container_frame)
        input_wrapper_layout.addStretch()
        search_layout.addWidget(self.input_wrapper, 0)
        
    # RESULTS AREA - TABS
        self.results_tabs = QTabWidget()
        
        # TEXT TABLE FOR TEXT TAB
        self.doc_table = QTableWidget()
        self.doc_table.setColumnCount(4)
        self.doc_table.setHorizontalHeaderLabels(["SCORE", "NAME", "TYPE", "PATH"])
        self.doc_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.doc_table.horizontalHeader().setVisible(False)
        self.doc_table.verticalHeader().setVisible(False)
        self.doc_table.setShowGrid(False)
        self.doc_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.doc_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.doc_table.itemClicked.connect(self.open_results_dialog_from_table)
        self.doc_table.hideColumn(0)
        self.doc_table.hideColumn(2)
        self.doc_table.hideColumn(3)
        self.doc_table.setStyleSheet(f"""
            QTableWidget {{
                background-color: {BG_DARK};
                border: none;
                outline: 0;
            }}
            QTableWidget::item {{ border: none; padding-left: 25px; }}
            QTableWidget::item:selected {{ background-color: {BG_DARK}; border: none; outline: none; }}
            QTableWidget::item:hover {{ background-color: {BG_LIGHT}; border: none; outline: none; }}
            QTableWidget::item:selected:hover {{ background-color: {BG_LIGHT}; border: none; outline: none; }}
            QTableWidget::item:pressed {{ background-color: {BG_DARK}; border: none; outline: none; }}
        """)
        
        # IMAGE LIST FOR IMAGE TAB
        self.image_list = QListWidget()
        self.image_list.setViewMode(QListWidget.IconMode)
        self.image_list.setIconSize(QSize(180, 140))
        self.image_list.setResizeMode(QListWidget.Adjust)
        self.image_list.setSpacing(15)
        self.image_list.setWordWrap(True)
        self.image_list.setTextElideMode(Qt.ElideNone)
        self.image_list.setMovement(QListWidget.Static)
        self.image_list.setGridSize(QSize(200, 200))
        self.image_list.itemClicked.connect(self.open_results_dialog_from_list)
        self.image_list.setStyleSheet(f"""
            QListWidget {{ background-color: {BG_DARK}; border: none; outline: 0; selection-background-color: {BG_DARK}; padding-left: 35px; }}
            QListWidget::item {{ border: none; color: {TEXT_MAIN}; padding: 20px; border-radius: 0px;}}
            QListWidget::item:selected {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; border: none; outline: none;}}
            QListWidget::item:hover {{ background-color: {BG_LIGHT}; color: {TEXT_MAIN}; border: none; outline: none; }}
            QListWidget::item:selected:hover {{ background-color: {BG_LIGHT}; border: none; outline: none; }}
            QListWidget::item:pressed {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; border: none; outline: none; }}
        """)
        # For hover events
        self.image_list.setMouseTracking(True)
        self.image_list.viewport().installEventFilter(self)

        # TEXT AREA FOR AI INSIGHTS TAB
        self.rag_page = QWidget()
        rag_layout = QVBoxLayout(self.rag_page)
        self.llm_output = FileLinkBrowser()
        self.llm_output.setOpenExternalLinks(True)
        self.llm_output.setPlaceholderText(" ")
        self.llm_output.setStyleSheet(f"""
            QTextBrowser {{
                background-color: {BG_DARK};
                border: none;
                padding: 15px;
                color: {TEXT_MAIN};
            }}
        """)
        # To adjust the file link text color to the accent color
        self.llm_output.document().setDefaultStyleSheet(f"""
            a {{
                color: {ACCENT_COLOR};
                text-decoration: none;
                font-weight: bold;
            }}
        """)
        rag_layout.addWidget(self.llm_output, 1)

        # ADD TABS TO THE TAB WIDGET
        self.results_tabs.addTab(self.doc_table, "Documents")
        self.results_tabs.addTab(self.image_list, "Images")
        self.results_tabs.addTab(self.rag_page, "AI Insights")
        # Set cursor for tab bar
        self.results_tabs.tabBar().setCursor(Qt.PointingHandCursor)
        # Add it to the main search page
        search_layout.addWidget(self.results_tabs, 1)

    # SETTINGS PAGE
        self.page_settings = QWidget()
        # Use a VBox for the main page
        self.settings_main_layout = QVBoxLayout(self.page_settings)
        self.settings_main_layout.setContentsMargins(0, 0, 0, 0)

    # LOGGING PAGE
        self.page_logs = QWidget()
        logs_layout = QVBoxLayout(self.page_logs)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.document().setMaximumBlockCount(1000)  # Only show last 1000 messages
        self.log_output.setFont(QFont("Consolas", 9))
        self.log_output.setStyleSheet(f"QTextEdit {{ background-color: {BG_INPUT}; color: #ccc; border: none; selection-color: white; }}")  # selection-background-color: blue; 
        logs_layout.addWidget(self.log_output)

        # Add ALL pages to the stack
        self.stack.addWidget(self.page_search)
        self.stack.addWidget(self.page_settings)
        self.stack.addWidget(self.page_logs)
        main_layout.addWidget(self.stack)

    # STATUS BAR
        self.status_bar = QStatusBar()
        self.setStatusBar(self.status_bar)
        self.status_bar.showMessage("Initializing system connection...")

    # final touches
        self.search_input.setFocus()

    def adjust_search_input_height(self):
        content_height = int(self.search_input.document().size().height())
        new_height = content_height + (self.search_input_vertical_padding * 2)
        if new_height >= self.search_input_max_height:
            final_height = self.search_input_max_height + (self.search_input_vertical_padding * 2)
        elif new_height < self.search_input_min_height:
            final_height = self.search_input_min_height + (self.search_input_vertical_padding * 2)
        else:
            final_height = new_height
        self.search_input.setFixedHeight(final_height)

    def reauthorize_drive(self):
        logger.info("Reauthorizing Google Drive...")
        def reauth_worker():
            token_path = DATA_DIR / "token.json"
            if token_path.exists():
                os.remove(token_path)
            try:
                self.drive_service = get_drive_service(self.config)
            except Exception as e:
                logger.error(f"[ERROR] Failed to get Drive service: {e}")
                self.drive_service = None
        threading.Thread(target=reauth_worker, daemon=True).start()

    def eventFilter(self, obj, event):
        """Automatically found and installed by 'QApplication.instance().installEventFilter(self)'"""
        # Image list hover effect
        if event.type() == QEvent.MouseMove:
            if obj is self.image_list.viewport():
                item = self.image_list.itemAt(event.pos())
                cursor = Qt.PointingHandCursor if item else Qt.ArrowCursor
                self.image_list.setCursor(cursor)

        # Konami Code - track last 10 keys (non-blocking)
        if event.type() == QEvent.KeyPress:
            # Only track keys when not auto-repeating and outside cooldown
            if not event.isAutoRepeat():
                current_time = time.time()
                if current_time - self.last_key_time >= 0.05:
                    self.last_key_time = current_time
                    key = event.key()
                    # Add to history
                    self.key_history.append(key)
                    # Check if last N keys match the konami code
                    if list(self.key_history) == self.konami_code:
                        self.trigger_secret()
                        self.key_history.clear()
        # Enter Key Handling in Search Input - to run search, or to insert newline when shift is held
        if obj is self.search_input and event.type() == QEvent.KeyPress:
            key = event.key()
            modifiers = event.modifiers()
            if key == Qt.Key_Return or key == Qt.Key_Enter:
                if modifiers == Qt.ShiftModifier:
                    # Allow QTextEdit to insert a newline
                    return super().eventFilter(obj, event)
                else:
                    self.run_search()
                    return True

        return super().eventFilter(obj, event)

    def trigger_secret(self):
        """For Konami Code"""
        logger.info("!! SECRET ACTIVATED !!")
        self.status_bar.showMessage("👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️👁️", 10000)

    def create_file_cell_widget(self, name_text, path_text):
        """Creates a custom widget with Name (top) and Path (bottom/italic)"""
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(5, 5, 5, 5)
        widget.setCursor(Qt.PointingHandCursor)
        
        # 1. File Name Label
        lbl_name = QLabel(name_text)
        lbl_name.setStyleSheet(f"font-size: 14px; font-weight: bold; color: {TEXT_MAIN}; border: none; background: transparent;")
        
        # 2. Path Label (Small, Italic, Dimmed)
        lbl_path = QLabel(path_text)
        lbl_path.setStyleSheet("font-size: 11px; font-style: italic; color: #666; border: none; background: transparent;")
        
        layout.addWidget(lbl_name)
        layout.addWidget(lbl_path)
        return widget

    def switch_page(self, index):
        self.stack.setCurrentIndex(index)
        self.btn_search.setChecked(index == 0)
        self.btn_settings.setChecked(index == 1)
        self.btn_logs.setChecked(index == 2)

    @Slot(dict, int)
    def update_status_bar(self, stats, total_files):
        """Updates the status bar with current database stats. Formats nicely."""
        # Helper to format each section
        def fmt_stat(name, model_key, data):
            # 1. Status Icon
            is_loaded = self.models.get(model_key) and self.models[model_key].loaded
            icon = "✦" if is_loaded else "✧"
            # 2. Safe Data Extraction
            p = data.get("PENDING", 0)
            d = data.get("DONE", 0)
            f = data.get("FAILED", 0)
            r = data.get("DB_ROWS", 0)
            total = d + f + p
            if total == 0:
                pct = 100.00
            else:
                pct = 100 * ((d + f) / total) 
            return f"[{name} {icon}] {pct:.2f}%"
        # Build the sections
        s_ocr = fmt_stat("OCR", "ocr", stats.get("OCR", {}))
        # Manually sum the counts so the UI treats all embed jobs as one big job queue
        e1 = stats.get("EMBED", {})
        e2 = stats.get("EMBED_LLM", {})
        combined_embed = {
            "PENDING": e1.get("PENDING", 0) + e2.get("PENDING", 0),
            "DONE":    e1.get("DONE", 0)    + e2.get("DONE", 0),
            "FAILED":  e1.get("FAILED", 0)  + e2.get("FAILED", 0)
        }
        s_emb = fmt_stat("EMBED", "text", combined_embed)
        s_llm = fmt_stat("LLM", "llm", stats.get("LLM", {}))
        # Final Assembly with nice spacing
        msg = f"FILES: {total_files:,}    |    {s_ocr}    |    {s_emb}    |    {s_llm}"        
        self.status_bar.showMessage(msg)

    @Slot(str)
    def display_log_message_safe(self, message):
        """Thread-safe slot to append text to the log display."""
        self.log_output.append(message)

    def remove_attachment(self):
        """Clears the current attachment and resets the UI."""
        self.attached_file_path = None
        self.btn_attach.setToolTip("Attach File")
        # Reset to default paperclip icon
        self.btn_attach.setIcon(qta.icon('ph.paperclip-bold')) 
        logger.info("Attachment removed.")

    def set_attachment(self, path):
        """Sets the attachment path and updates the UI to the 'Remove' state."""
        if not path: 
            return
        self.attached_file_path = str(path)
        filename = Path(path).name
        # Update Tooltip to show what is attached
        self.btn_attach.setToolTip(f"Remove attachment: {filename}")
        # Change Icon to an 'X' (indicating clicking it will remove)
        self.btn_attach.setIcon(qta.icon('mdi.close')) 
        logger.info(f"Attached file: {path}")

    def find_attachment(self):
        """Opens the file picker and returns the attachment if one is chosen."""
        # 1. Get extensions from config
        text_exts = self.config.get('text_extensions', [])
        img_exts = self.config.get('image_extensions', [])
        all_exts = text_exts + img_exts
        # 2. Format filters for Qt
        def fmt(ext_list):
            return " ".join([f"*{ext}" for ext in ext_list])
        filters = [
            f"All Supported Files ({fmt(all_exts)})",
            f"Text Documents ({fmt(text_exts)})",
            f"Images ({fmt(img_exts)})",
            "All Files (*)"
        ]
        # 3. Open Dialog
        path, _ = QFileDialog.getOpenFileName(
            self, 
            "Attach File to Search", 
            "", 
            ";;".join(filters)
        )
        # 4. Pass result to setter
        if path:
            return path

    def handle_attach(self):
        """Main button handler: Toggles between Find and Remove."""
        if self.attached_file_path:
            self.remove_attachment()
        else:
            attachment = self.find_attachment()
            self.set_attachment(attachment)

    def handle_filter(self):
        """Opens the Advanced Search Dialog."""
        # Open dialog with current state
        dialog = AdvancedSearchDialog(self.folder_filter, self.source_filter, self)  # Don't ask me why self is last
        if dialog.exec():
            # Retrieve new state
            self.folder_filter = dialog.folder_path
            self.source_filter = dialog.source_filter
            # Update Button Visuals
            self.update_filter_icon()

    def update_filter_icon(self):
        """Updates the filter button to show active/inactive state."""
        # Check if any source is disabled (False)
        sources_modified = not all(self.source_filter.values())
        
        # Filter is active if: Folder is set OR Sources are modified
        has_filter = (self.folder_filter is not None) or \
                     sources_modified

        if has_filter:
            # Make it a little green
            self.btn_filter.setIcon(qta.icon('mdi.filter-variant', color=ACCENT_COLOR))
            
            # Build a helpful tooltip
            tips = []
            if self.folder_filter: 
                tips.append(f"Folder: {Path(self.folder_filter).name}")
            if sources_modified:
                # Show which ones are turned OFF
                disabled = [k for k, v in self.source_filter.items() if not v]
                tips.append(f"Disabled: {', '.join(disabled)}")
                
            self.btn_filter.setToolTip(" | ".join(tips))
        else:
            # Default State
            self.btn_filter.setIcon(self.filter_icon) 
            self.btn_filter.setToolTip("Filter Search")

    def run_search(self):
        """The main entry point to start a search operation. Handles UI updates, worker management, and information collection."""
        query = self.search_input.toPlainText().strip()
        if not query and not self.attached_file_path:
            # This gives user the ability to get a clean slate, optional feature might remove
            self.doc_table.setRowCount(0)
            self.image_list.clear()
            self.llm_output.clear()
            return
        # Initialize data class to coordinate critical search information
        searchfacts = SearchFacts(query=query, attachment_path=self.attached_file_path, folder_filter=self.folder_filter, source_filter=self.source_filter)
        # 1. Clear UI immediately (Instant feedback)
        self.btn_send.setIcon(self.send_spin_icon)
        self.doc_table.setRowCount(0)
        self.image_list.clear()
        self.llm_output.clear()
        # 2. Stop previous worker if it's still running (prevents race conditions)
        if self.workers:
            # Loop over a copy [:] to remove items safely
            for w in self.workers[:]:
                if (isinstance(w, SearchWorker) or isinstance(w, LLMWorker)) and w.isRunning():
                    logger.info(f"Cancelling active worker: {w}")
                    # A. Cut the wire so it doesn't trigger the next step (e.g. Search -> RAG)
                    try: w.finished.disconnect() 
                    except: pass
                    # self.status_bar.showMessage("Stopping previous search...")
                    w.stop()
                    self.workers.remove(w)
        # 3. Initialize Streaming Worker - does the actual search
        worker = SearchWorker(self.search_engine, searchfacts)
        # 4. Connect the split signals that return the results
        worker.text_ready.connect(self.on_text_ready)
        worker.image_stream.connect(self.on_image_stream)
        # 5. Make sure to cleanup when totally done
        worker.finished.connect(lambda: self.cleanup_worker(worker))
        if self.models['llm'].loaded:
            # Start RAG after search completes. Happens after because RAG needs search results.
            worker.finished.connect(lambda: self.start_rag_generation(searchfacts))
        else:
            # If no LLM, reset the send button when SearchWorker is done.
            worker.finished.connect(lambda: self.btn_send.setIcon(self.send_icon))
        # 6. Start the worker!    
        self.workers.append(worker)
        worker.start()

    @Slot(list)
    def on_text_ready(self, text_res):
        """Populates the text table all at once (since it's fast). Uses table formatting."""
        self.doc_table.setRowCount(len(text_res))
        for row, item in enumerate(text_res):
            try:
                self.doc_table.setRowHeight(row, 60)
                # Score Item
                score = QTableWidgetItem(f"{item['score']:.2f}")
                score.setForeground(QBrush(QColor(ACCENT_COLOR)))
                score.setTextAlignment(Qt.AlignCenter)
                self.doc_table.setItem(row, 0, score)
                # Name Item
                name_text = Path(item['path']).stem
                path_text = str(item['path'])
                name_item = QTableWidgetItem("") 
                self.doc_table.setItem(row, 1, name_item)
                cell_widget = self.create_file_cell_widget(name_text, path_text)
                self.doc_table.setCellWidget(row, 1, cell_widget)
                # Type Item
                type_ = QTableWidgetItem(item.get('match_type', 'Mix').upper())
                self.doc_table.setItem(row, 2, type_)
                path_item = QTableWidgetItem(path_text)
                path_item.setData(Qt.UserRole, item)
                self.doc_table.setItem(row, 3, path_item)
            except Exception as e:
                logger.error(f"Error displaying text result: {e}")

    @Slot(dict, QImage)
    def on_image_stream(self, item, qimg):
        """Adds a SINGLE image to the list as soon as it arrives"""
        try:
            # Check if the qimg passed is valid
            if qimg and not qimg.isNull():
                pixmap = QPixmap.fromImage(qimg)
            else:
                # Fallback for failed loads
                pixmap = QPixmap(180, 140)
                pixmap.fill(QColor(BG_LIGHT))
            # Create the QListWidgetItem with icon and text
            icon = QIcon()
            icon.addPixmap(pixmap, QIcon.Mode.Normal)
            icon.addPixmap(pixmap, QIcon.Mode.Selected)  # This was needed to fix an error where the highlighted icon was magenta
            list_item = QListWidgetItem(icon, f"{Path(item['path']).name}")
            font = QFont()
            font.setBold(True)
            font.setPointSize(10.5)
            list_item.setFont(font)
            list_item.setData(Qt.UserRole, item)
            self.image_list.addItem(list_item)            
        except Exception as e: 
            logger.error(f"Error displaying stream image: {e}")

    def start_rag_generation(self, searchfacts):
        # A. Stop any existing LLM generation
        if self.workers:
            for w in self.workers[:]:
                if isinstance(w, LLMWorker) and w.isRunning():
                    try: w.finished.disconnect()
                    except: pass
                    w.stop()
                    self.workers.remove(w)
        # B. Clear Output
        self.accumulated_markdown = ""
        # C. Start the Worker
        worker = LLMWorker(self.models['llm'], searchfacts, self.config)
        worker.chunk_ready.connect(self.update_llm_output)  # Next function
        worker.finished.connect(lambda: self.cleanup_worker(worker))
        worker.finished.connect(lambda: self.btn_send.setIcon(self.send_icon))  # Reset icon when done
        self.workers.append(worker)
        worker.start()

    @Slot(str)
    def update_llm_output(self, chunk):
        """Handles the LLM response Stream (The Slot). Updates the QTextBrowser while preserving scroll state. Uses cryptic regex to fix local file links."""
        if not chunk: return
        self.accumulated_markdown += chunk
        # 1. Capture Scroll State BEFORE updating
        sb = self.llm_output.verticalScrollBar()
        # "At bottom" means user is within 10 pixels of the max scroll - use this to stop annoying forced scrolling
        was_at_bottom = sb.value() >= (sb.maximum() - 10)
        previous_scroll_val = sb.value()
        # --- HELPER: Convert Windows path to valid URI ---
        def path_to_uri(match):
            text = match.group(1)   
            raw_path = match.group(2)
            try:
                path_obj = Path(raw_path)
                if not path_obj.is_absolute():
                     path_obj = path_obj.resolve()
                uri = path_obj.as_uri()
                return f"{text}({uri})"
            except Exception:
                return match.group(0)
        # Cryptic regex to fix links. I don't know how it works; it just does.
        pat = r'(\[[^\]]*\])\(([^()]*(?:\([^()]*\)[^()]*)*)\)'
        fixed_md = re.sub(pat, path_to_uri, self.accumulated_markdown)
        # Convert to HTML markdown
        html = markdown.markdown(fixed_md)
        # 2. Update the Text
        self.llm_output.setHtml(html)
        # 3. Restore Scroll State (Smart Scroll)
        if was_at_bottom:
            # If at the bottom, keep auto-scrolling to show new text
            sb.setValue(sb.maximum())
        else:
            # If reading earlier text, stay there (don't jump)
            sb.setValue(previous_scroll_val)

    # --- MODEL & TRAY LOGIC ---

    def toggle_model(self, key):
        """Toggles loading/unloading of a model in a separate thread using ModelToggleWorker."""
        current = False
        # Find current state
        if key == 'ocr': current = self.models['ocr'].loaded
        elif key == 'embed': current = self.models['text'].loaded
        elif key == 'llm': current = self.models['llm'].loaded
        elif key == 'screenshotter': current = self.models['screenshotter'].loaded
        # Decide action based on state
        action = "unload" if current else "load"
        # Start the worker
        worker = ModelToggleWorker(self.models, key, action)
        worker.finished.connect(self.on_model_toggle_done)
        worker.finished.connect(lambda: self.cleanup_worker(worker))
        self.workers.append(worker)
        worker.start()

    @Slot(str, bool)
    def on_model_toggle_done(self, key, action, success):
        """Updates the UI after model load/unload is complete. Also resumes pending tasks if loading succeeded."""
        # Refresh the tray menu text to reflect the new state
        self.update_tray_menu()
        self.update_button_states()
        # Resume pending tasks
        if success and action == "load":
            # 'embed' wakes up both standard embedding tasks AND summary embedding tasks
            key_map = {
                'ocr': ['OCR'], 
                'embed': ['EMBED', 'EMBED_LLM'], 
                'llm': ['LLM']
            }
            task_types = key_map.get(key, [])
            # Spawn a single background thread to wake them up sequentially
            def wake_up_worker():
                for t_type in task_types:
                    self.orchestrator.resume_pending(t_type)
            if task_types:
                threading.Thread(target=wake_up_worker, daemon=True).start()

    def setup_tray(self):
        """Creates the system tray icon and menu with actions. Should probably be higher up."""
        self.tray_icon = QSystemTrayIcon(self)
        self.tray_icon.setIcon(QIcon(self.icon_path))
        self.tray_menu = QMenu()
        custom_style = f"""
            QMenu {{
                background-color: {BG_DARK};
                border-radius: 6px;
                padding-top: 4px;
                padding-bottom: 4px;
            }}
            QMenu::item {{
                color: {TEXT_MAIN};
                padding: 6px 7px;
                border-radius: 0px;
            }}
            QMenu::item:selected {{
                border-radius: 0px;
                background-color: {BG_LIGHT};
            }}
            QMenu::separator {{
                height: 1px;
                margin-left: 10px;
                margin-right: 10px;
                background-color: {ACCENT_COLOR};
            }}
        """
        self.tray_menu.setStyleSheet(custom_style)
        # SHOW APPLICATION BUTTON
        show_action = QAction("Open", self)
        font = QFont()
        font.setBold(True)  # Make it bold
        show_action.setFont(font)
        show_action.triggered.connect(self.show)
        # Screenshotting software ("Windows Recall")
        self.act_screenshot = QAction("Start Screen Capture", self)
        self.act_screenshot.triggered.connect(lambda: self.toggle_model('screenshotter'))
        # LOAD/UNLOAD MODEL BUTTONS
        self.act_ocr = QAction("Load OCR", self)
        self.act_ocr.triggered.connect(lambda: self.toggle_model('ocr'))
        self.act_embed = QAction("Load Embedders", self)
        self.act_embed.triggered.connect(lambda: self.toggle_model('embed'))
        self.act_llm = QAction("Load LLM", self)
        self.act_llm.triggered.connect(lambda: self.toggle_model('llm'))
        # QUIT BUTTON
        quit_action = QAction("Quit", self)
        show_action.setFont(font)
        quit_action.setFont(font)
        quit_action.triggered.connect(QApplication.quit)
        # Set it all up
        self.tray_menu.addAction(show_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.act_screenshot)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.act_ocr)
        self.tray_menu.addAction(self.act_embed)
        self.tray_menu.addAction(self.act_llm)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(quit_action)
        # Attach the menu to the tray icon
        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
        # Initial State Check
        self.update_tray_menu()

    def update_tray_menu(self):
        """Updates the labels based on loaded state"""
        # Find current states
        if self.models:
            ocr_loaded = self.models['ocr'].loaded
            embed_loaded = self.models['text'].loaded
            llm_loaded = self.models['llm'].loaded
            screenshotter_loaded = self.models['screenshotter'].loaded
        # Set the text accordingly for the 4 buttons
        else:
            ocr_loaded = False
            embed_loaded = False
            llm_loaded = False
            screenshotter_loaded = False
        self.act_screenshot.setText("Stop Screen Capture" if screenshotter_loaded else "Start Screen Capture")
        self.act_ocr.setText("Unload OCR" if ocr_loaded else "Load OCR")
        self.act_embed.setText("Unload Embedders" if embed_loaded else "Load Embedders")
        self.act_llm.setText("Unload LLM" if llm_loaded else "Load LLM")

    def on_tray_activated(self, reason):
        """Just manages visibility."""
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()

    def closeEvent(self, event):
        """Just manages minimizing to tray instead of quitting."""
        if self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            event.accept()

    def open_results_dialog_from_table(self, item):
        """Allows the user to open a detailed results dialog from the text table view."""
        # The data is stored in Column 3 (the hidden path column)
        row = item.row()
        path_item = self.doc_table.item(row, 3)
        data = path_item.data(Qt.UserRole)
        
        dialog = ResultDetailsDialog(data, self)
        dialog.exec()

    def open_results_dialog_from_list(self, item):
        """Allows the user to open a detailed results dialog from the image list view."""
        # The data is stored directly in the item's UserRole
        data = item.data(Qt.UserRole)
        
        dialog = ResultDetailsDialog(data, self)
        dialog.exec()

    def start(self):
        self.orchestrator, self.watcher, self.search_engine, self.models, self.config = backend_setup()
        self.create_settings_page()
        self.update_button_states()
        self.show()
        # Start stats polling (every N seconds) for the status bar
        if hasattr(self.search_engine, 'db'):
            self.stats_thread = StatsWorker(self.search_engine.db)
            self.stats_thread.stats_updated.connect(self.update_status_bar)
            self.stats_thread.start()

    def restart(self):
        logger.info("RELOADING BACKEND === RELOADING BACKEND === RELOADING BACKEND")
        self.watcher.stop()
        self.orchestrator.stop()
        for key, model in self.models.items():
            model.unload()
        # Clear existing widgets from the settings layout
        while self.settings_main_layout.count():
            item = self.settings_main_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        self.stats_thread.stop()
        self.orchestrator = None
        self.watcher = None
        self.search_engine = None
        self.models = None
        self.config = None
        self.start()

    # --- CONFIG & DB LOGIC ---

    def create_settings_page(self):
        # Scroll Area (because of many config options)
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setStyleSheet(f"""
            /* 1. The Scroll Area Itself */
            QScrollArea {{
                background-color: {BG_DARK};
                border: none;
            }}
            /* 2. The Content Widget inside the Scroll Area */
            QScrollArea > QWidget > QWidget {{
                background-color: {BG_DARK};
            }}
            /* 3. The Vertical Scrollbar */
            QScrollBar:vertical {{
                border: none;
                background: {BG_DARK};
                width: 14px;
                margin: 0px;
            }}
            QScrollBar::handle:vertical {{
                background-color: {BG_LIGHT};
                min-height: 30px;
                border-radius: 7px;
                margin: 2px; /* Creates a nice padding around the handle */
            }}
            QScrollBar::handle:vertical:hover {{
                background-color: {OUTLINE};
            }}
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {{
                height: 0px;
            }}
            QScrollBar::add-page:vertical, QScrollBar::sub-page:vertical {{
                background: none;
            }}
        """)
        scroll_content = QWidget()
        scroll.setWidget(scroll_content)
        self.settings_main_layout.addWidget(scroll)
               
        # Configure content inside the scroll area
        self.settings_layout = QVBoxLayout(scroll_content)
        self.settings_layout.setContentsMargins(50, 50, 50, 50)
        self.settings_layout.setSpacing(0)
        self.settings_layout.setAlignment(Qt.AlignTop)

    # SETTINGS SECTION 1: LIVE CONTROLS (No Restart Required)
        # A. Model Toggles
        self.btn_ocr_toggle = self.add_live_setting_row("Optical Character Recognition (OCR)", "Extracts text from images for lexical (keyword) search. Lexical search is always enabled.",
                                  lambda: self.toggle_model('ocr'), color=TEXT_MAIN)
        self.btn_embed_toggle = self.add_live_setting_row("Embedding Models", "Create embeddings from documents and images for semantic search. Indexes text for lexical search. When the model is loaded, semantic search is enabled.",
                                  lambda: self.toggle_model('embed'), color=TEXT_MAIN)
        self.btn_llm_toggle = self.add_live_setting_row("Large Language Model (LLM)", "Generates summaries from documents and images, which are then embedded for semantic search and indexed for lexical search. When the model is loaded, AI Insights are generated for search results.",
                                  lambda: self.toggle_model('llm'), color=TEXT_MAIN)
        self.btn_screenshotter_toggle = self.add_live_setting_row("Screen Capture", f"Takes screenshots every {self.config.get('screenshot_interval', 'N')} seconds.", 
                                  lambda: self.toggle_model('screenshotter'), color=TEXT_MAIN)

        self.add_live_setting_row("Open Data Folder", "Open the local AppData folder where data records are stored. To reset all settings, delete config.json here.",
                                  lambda: os.startfile(DATA_DIR), color=TEXT_MAIN)
        # B. External Auth
        self.add_live_setting_row("Reauthorize Google Drive", "Retry the Google OAuth flow by deleting token.json from the local AppData folder.",
                                  lambda: self.reauthorize_drive(), color=TEXT_MAIN)
        # C. Database Actions
        self.add_live_setting_row("Retry Tasks", "Set all 'FAILED' tasks back to 'PENDING' for retry.",
                                  lambda: self.run_db_action('retry_failed'), color="#d5b462")
        self.add_live_setting_row("Reset OCR Data", "Delete data from OCR tasks and then re-queue OCR tasks.",
                                  lambda: self.run_db_action('reset_service', ['OCR']), color="#e06c75")
        self.add_live_setting_row("Reset Embeddings", "Delete data from EMBED tasks and then re-queue EMBED tasks.",
                                  lambda: self.run_db_action('reset_service', ['EMBED']), color="#e06c75")
        self.add_live_setting_row("Reset LLM Data", "Delete data from LLM tasks and then re-queue LLM tasks.",
                                  lambda: self.run_db_action('reset_service', ['LLM']), color="#e06c75")
        self.settings_layout.addSpacing(15)  # Space between sections
    
    # SETTINGS SECTION 2: CONFIGURATION
        self.config_widgets = {} # To store inputs for saving
        # Iterate over config keys to create rows
        # Filter this list to hide internal keys!
        ignored_keys = ['quality_weight'] 
        for key, value in self.config.items():
            if key not in ignored_keys:
                self.add_config_row(key, value)

        self.settings_layout.addSpacing(20)
        
        # Large Save Button
        btn_save = QPushButton("Save Settings and Reload")
        btn_save.setFixedHeight(45)
        btn_save.setFixedWidth(275)
        hover_tint = get_tint(ACCENT_COLOR, 0.1)
        btn_save.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {ACCENT_COLOR}; text-align: center; padding: 4px; border-radius: 0px; border: 1px solid {hover_tint}; }}
            QPushButton:hover {{ text-decoration: underline; background-color: {hover_tint}; }}
        """)
        def save_and_restart():
            self.save_config()
            self.status_bar.showMessage("Reloading backend...", 5000)
            self.restart()
        btn_save.clicked.connect(save_and_restart)
        btn_save.setCursor(Qt.PointingHandCursor)
        self.settings_layout.addWidget(btn_save, 0, Qt.AlignCenter)

        self.settings_layout.addSpacing(17)

        # Quit Application Button
        btn_quit = QPushButton("Quit")
        btn_quit.setFixedHeight(35)
        btn_quit.setFixedWidth(120)
        hover_tint = get_tint(TEXT_MAIN, 0.1)
        btn_quit.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {TEXT_MAIN}; text-align: center; padding: 4px; border-radius: 0px; border: 1px solid {hover_tint}; }}
            QPushButton:hover {{ text-decoration: underline; background-color: {hover_tint}; }}
        """)
        btn_quit.clicked.connect(QApplication.quit)
        btn_quit.setCursor(Qt.PointingHandCursor)
        self.settings_layout.addWidget(btn_quit, 0, Qt.AlignCenter)

    def save_config(self):
        """Reads values from UI inputs and writes to config.json"""        
        # Update self.config from the UI fields stored.
        for key, widget in self.config_widgets.items():
            val = widget.text().strip()
            # 1. Handle Booleans
            if val.lower() == 'true':
                val = True
            elif val.lower() == 'false':
                val = False
            else:
                try:
                    # 2. Try to parse as a Python literal (List, Dict, Number)
                    val = ast.literal_eval(val)
                except (ValueError, SyntaxError):
                    # 3. Fallback to string if it's just plain text
                    pass
            self.config[key] = val
        # Write to file.
        try:
            with open(DATA_DIR / "config.json", "w") as f:
                json.dump(self.config, f, indent=4)
            self.status_bar.showMessage("Configuration saved.", 5000)
        except Exception as e:
            self.status_bar.showMessage(f"Failed to save config: {e}", 5000)

    def run_db_action(self, action_type, service_keys=[]):
        """Runs the DB worker from the Settings page to do certain actions, with a confirmation for destructive actions."""
        # 1. Check if this is a 'Danger' action (Resetting service data)
        if action_type == 'reset_service':
            from PySide6.QtWidgets import QMessageBox
            # Create the 'Are you sure?' box
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Confirm Data Reset")
            msg_box.setText(f"Are you sure you want to reset {', '.join(service_keys)} data?")
            msg_box.setInformativeText("This will delete all processed records for this service and re-queue your files. This action cannot be undone.")
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            msg_box.setDefaultButton(QMessageBox.Cancel)
            # Show the box and capture the user's choice
            choice = msg_box.exec()
            if choice == QMessageBox.Cancel:
                return  # Exit early; do nothing
        # 2. Proceed with the worker if confirmed (or if it's not a danger action)
        worker = DatabaseActionWorker(self.search_engine.db, self.orchestrator, action_type, service_keys)
        worker.finished.connect(lambda msg: self.status_bar.showMessage(msg, 4000))
        worker.finished.connect(lambda: self.cleanup_worker(worker))
        self.workers.append(worker)
        worker.start()

    def add_live_setting_row(self, title, subtitle, callback, color):
        """Creates a row with Title, Subtitle, and an Action Button for the settings."""
        frame = QFrame()
        frame.setStyleSheet(f"background-color: {BG_DARK}; border-radius: 0px; border-bottom: 1px solid {OUTLINE};")
        frame.setMinimumHeight(60)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(15, 15, 15, 15)
        # Text
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setAlignment(Qt.AlignVCenter)
        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 11, QFont.Bold))
        t.setStyleSheet("border: none; background: transparent;")
        s = QLabel(subtitle)
        s.setWordWrap(True)
        s.setFont(QFont("Segoe UI", 8))
        s.setStyleSheet("color: #888; border: none; background: transparent;")
        text_layout.addWidget(t)
        text_layout.addWidget(s)
        # Button
        btn_text_color = "white"
        btn = QPushButton("Execute")
        btn.setFixedWidth(120)
        btn.setFixedHeight(35)
        hover_tint = get_tint(color, 0.1)
        btn.setStyleSheet(f"""
            QPushButton {{ background-color: transparent; color: {color}; text-align: center; padding: 4px; border-radius: 0px; border: 1px solid {hover_tint}; }}
            QPushButton:hover {{ text-decoration: underline; background-color: {hover_tint}; }}
        """)
        btn.clicked.connect(callback)
        btn.setCursor(Qt.PointingHandCursor)
        # Assemble
        layout.addLayout(text_layout, 1)
        layout.addWidget(btn)
        self.settings_layout.addWidget(frame)
        return btn  # So that the text can be updated later

    def add_config_row(self, key, value):
        """Creates a row with Key Label and an Input Field based on config.json"""
        frame = QFrame()
        frame.setStyleSheet(f"background-color: {BG_DARK}; border-radius: 6px;")
        frame.setMinimumHeight(60)
        layout = QVBoxLayout(frame)
        layout.setContentsMargins(15, 10, 15, 10)
        # Text
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setAlignment(Qt.AlignVCenter)
        title = CONFIG_DATA[key][0]
        subtitle = CONFIG_DATA[key][1]
        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 11, QFont.Bold))
        t.setStyleSheet("border: none; background: transparent;")
        s = QLabel(subtitle)
        s.setWordWrap(True)
        s.setFont(QFont("Segoe UI", 8))
        s.setStyleSheet("color: #888; border: none; background: transparent;")
        text_layout.addWidget(t)
        text_layout.addWidget(s)
        # Input
        inp = QLineEdit(str(value))
        inp.setStyleSheet(f"""
            background-color: {BG_INPUT}; 
            border: 0px solid {OUTLINE}; 
            border-radius: 0px; 
            color: {ACCENT_COLOR};
            padding: 4px;
        """)
        inp.setCursor(Qt.IBeamCursor)
        # Store for saving later
        self.config_widgets[key] = inp
        # Assemble
        layout.addLayout(text_layout, 1)
        layout.addWidget(inp)
        self.settings_layout.addWidget(frame)

    def update_button_states(self):
        """Updates the Live Control buttons to match model state"""
        # OCR
        if self.models['ocr'].loaded:
            self.btn_ocr_toggle.setText("Unload")
        else:
            self.btn_ocr_toggle.setText("Load")
        # EMBED (Checks the 'text' model as proxy for both)
        if self.models['text'].loaded:
            self.btn_embed_toggle.setText("Unload")
        else:
            self.btn_embed_toggle.setText("Load")
        # LLM
        if self.models['llm'].loaded:
            self.btn_llm_toggle.setText("Unload")
        else:
            self.btn_llm_toggle.setText("Load")
        # Screenshotter / Screen Capture
        if self.models.get('screenshotter') and self.models['screenshotter'].loaded:
            self.btn_screenshotter_toggle.setText("Stop")
        else:
            self.btn_screenshotter_toggle.setText("Start")

    def cleanup_worker(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        worker.deleteLater() # clean up C++ resources