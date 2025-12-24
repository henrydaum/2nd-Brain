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
from collections import deque
# Internal
from guiWorkers import SearchWorker, StatsWorker, ModelToggleWorker, DatabaseActionWorker, LLMWorker, SearchFacts
from Parsers import get_drive_service
# Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QSystemTrayIcon, QMenu, QHeaderView,
    QLabel, QFrame, QAbstractItemView, QTabWidget, QStatusBar, QLineEdit, QScrollArea, QDialog, QTextBrowser, QFileDialog
)
from PySide6.QtCore import Qt, QSize, Signal, Slot, QEvent, QUrl
from PySide6.QtGui import QIcon, QPixmap, QFont, QColor, QBrush, QAction, QImage, QTextCursor, QDesktopServices
import qtawesome as qta
import markdown

logger = logging.getLogger("GUI")

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv('LOCALAPPDATA')) / "2nd Brain"

# --- VISUAL CONSTANTS ---
# ACCENT_COLOR = "#61afef"   # Blue
# ACCENT_COLOR = "#17616d"   # Second Brain blue
ACCENT_COLOR = "#cbe3a7"   # Second Brain green
BG_DARK      = "#1e2227"   # Main Background
BG_LIGHT     = "#282c34"   # Sidebar/Header
BG_MEDIUM    = "#23272d"
BG_INPUT     = "#181a1f"
TEXT_MAIN    = "#abb2bf"
OUTLINE      = "#3e4451"

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
    A dialog for configuring search filters like specific folders and negative terms.
    """
    def __init__(self, current_folder, current_negative, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Search Filters")
        self.setFixedSize(500, 250)
        
        # Store initial state
        self.folder_path = current_folder
        self.negative_query = current_negative
        
        # Styling
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; }}
            QLabel {{ font-size: 14px; font-weight: bold; color: {ACCENT_COLOR}; }}
            QLineEdit {{ 
                background-color: {BG_INPUT}; 
                border: 1px solid {OUTLINE}; 
                border-radius: 4px; 
                padding: 6px; 
                color: white; 
            }}
            QPushButton {{ 
                background-color: {BG_LIGHT}; 
                border: 1px solid {OUTLINE}; 
                border-radius: 4px; 
                padding: 6px 12px; 
                color: white;
            }}
            QPushButton:hover {{ background-color: {OUTLINE}; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # --- SECTION 1: FOLDER FILTER ---
        layout.addWidget(QLabel("Search In Specific Folder:"))
        
        folder_layout = QHBoxLayout()
        self.txt_folder = QLineEdit(self.folder_path if self.folder_path else "")
        self.txt_folder.setReadOnly(True)
        self.txt_folder.setPlaceholderText("All Folders (Default)")
        self.txt_folder.setStyleSheet("border: none;")
        
        # SINGLE TOGGLE BUTTON
        self.btn_folder_action = QPushButton()
        self.btn_folder_action.setFixedWidth(40) # Small square-ish button
        self.btn_folder_action.clicked.connect(self.handle_folder_toggle)
        
        folder_layout.addWidget(self.txt_folder)
        folder_layout.addWidget(self.btn_folder_action)
        layout.addLayout(folder_layout)

        # --- SECTION 2: NEGATIVE FILTER ---
        layout.addWidget(QLabel("Exclude Terms - Negative Search:"))
        self.txt_negative = QLineEdit(self.negative_query if self.negative_query else "")
        self.txt_negative.setPlaceholderText("e.g. blurry, draft, screenshots")
        self.txt_negative.setStyleSheet("border: none;")
        layout.addWidget(self.txt_negative)

        layout.addStretch()

        # --- ACTION BUTTONS ---
        btn_layout = QHBoxLayout()
        
        btn_apply = QPushButton("Done")
        btn_apply.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_DARK}; color: {ACCENT_COLOR}; border-radius: 4px; border: 1px solid {ACCENT_COLOR}; min-height: 30px; padding: 0 10px;}}
            QPushButton:hover {{ background-color: {ACCENT_COLOR}; color: {BG_DARK}; }}
        """)
        btn_apply.clicked.connect(self.apply_filters)
        
        btn_layout.addStretch()
        btn_layout.addWidget(btn_apply)
        layout.addLayout(btn_layout)

        # Initialize button state (Browse vs X)
        self.update_folder_button()

    def handle_folder_toggle(self):
        """
        If text is empty -> Open File Picker.
        If text exists -> Clear it.
        """
        if self.txt_folder.text():
            # State: CLEAR
            self.txt_folder.clear()
        else:
            # State: BROWSE
            folder = QFileDialog.getExistingDirectory(self, "Select Folder to Search In")
            if folder:
                self.txt_folder.setText(os.path.normpath(folder))
        
        # Update icon after action
        self.update_folder_button()

    def update_folder_button(self):
        """Updates the icon and tooltip based on whether a folder is selected."""
        if self.txt_folder.text():
            # Show "X" to clear
            self.btn_folder_action.setIcon(qta.icon('mdi.close'))
            self.btn_folder_action.setToolTip("Clear Selection")
        else:
            # Show "Folder" icon to browse
            self.btn_folder_action.setIcon(qta.icon('mdi.folder-open'))
            self.btn_folder_action.setToolTip("Browse...")

    def apply_filters(self):
        self.folder_path = self.txt_folder.text().strip() or None
        self.negative_query = self.txt_negative.text().strip() or None
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
            QLabel {{ color: {TEXT_MAIN}; font-size: 14px; }}
            QTextBrowser {{ background-color: {BG_LIGHT}; border: none; padding: 10px; color: {TEXT_MAIN}; font-size: 13px; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. Header (Filename)
        lbl_name = QLabel(Path(self.path).name)
        lbl_name.setFont(QFont("Segoe UI", 16, QFont.Bold))
        lbl_name.setStyleSheet(f"color: {ACCENT_COLOR};")
        layout.addWidget(lbl_name)

        # 2. Metadata Row (Score | Type)
        meta_layout = QHBoxLayout()
        score = item_data.get('score', 0.0)
        m_type = item_data.get('match_type', 'Unknown').upper()
        num_hits = item_data.get('num_hits', 1)
        
        lbl_meta = QLabel(f"<b>SCORE:</b> {score:.4f}   |   <b>TYPE:</b> {m_type}   |   HITS: {num_hits}")
        lbl_meta.setStyleSheet("color: #888;")
        meta_layout.addWidget(lbl_meta)
        meta_layout.addStretch()
        layout.addLayout(meta_layout)

        # 3. Content Area (The Text)
        self.text_browser = QTextBrowser()
        content = item_data.get('content', 'No preview text available.')
        self.text_browser.setText(content)
        layout.addWidget(self.text_browser)

        # 4. Buttons (Open File | Close)
        btn_layout = QHBoxLayout()
        btn_layout.setSpacing(10)
        btn_text_color = "white"
        btn_style = f"""
            QPushButton {{ 
                background-color: {BG_DARK}; 
                color: {btn_text_color}; 
                border-radius: 4px; 
                border: 1px solid {OUTLINE};
                min-height: 30px;
                padding: 0 10px;
            }}
            QPushButton:hover {{ background-color: {OUTLINE}; }}
        """

        # Attach File Button
        color = OUTLINE
        btn_attach_result = QPushButton("Attach")
        btn_attach_result.setCursor(Qt.PointingHandCursor)
        btn_attach_result.clicked.connect(self.attach_and_close)
        btn_attach_result.setStyleSheet(btn_style)
        
        # Copy Path Button
        color = OUTLINE
        btn_copy = QPushButton("Copy Path")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.path))
        btn_copy.setStyleSheet(btn_style)

        # Reveal in Explorer Button
        color = OUTLINE
        btn_reveal = QPushButton("Show Location")
        btn_reveal.setCursor(Qt.PointingHandCursor)
        btn_reveal.clicked.connect(self.reveal_in_explorer)
        btn_reveal.setStyleSheet(btn_style)

        # Open File
        color = OUTLINE
        btn_open = QPushButton("Open")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(self.open_file)
        btn_open.setStyleSheet(btn_style)
        
        # Close
        color = ACCENT_COLOR
        btn_close = QPushButton("Close")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_DARK}; color: {ACCENT_COLOR}; border-radius: 4px; border: 1px solid {color}; min-height: 30px; padding: 0 10px;}}
            QPushButton:hover {{ background-color: {color}; color: {BG_DARK}; }}
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

    def __init__(self, search_engine, orchestrator, models, config):
        super().__init__()
        self.search_engine = search_engine
        self.orchestrator = orchestrator
        self.models = models
        self.config = config
        self.drive_service = get_drive_service(self.config)  # Needed to open .gdoc attachments
        self.workers = []
        self.search_filter = None  # None means "Search Everything"
        self.negative_filter = ""  # Empty means "No Negative Filter"
        self.attached_file_path = None
        
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
        
        # Start stats polling (every N seconds) for the status bar
        if hasattr(self.search_engine, 'db'):
            self.stats_thread = StatsWorker(self.search_engine.db)
            self.stats_thread.stats_updated.connect(self.update_status_bar)
            self.stats_thread.start()

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
        self.btn_search.setIconSize(QSize(26, 26))
        self.btn_search.setFixedSize(42, 42)
        
        # SETTINGS BUTTON
        settings_icon = qta.icon('msc.settings-gear')
        self.btn_settings = QPushButton("")
        self.btn_settings.setToolTip("Settings")
        self.btn_settings.setIcon(settings_icon)
        self.btn_settings.setCheckable(True)
        self.btn_settings.clicked.connect(lambda: self.switch_page(1))
        self.btn_settings.setIconSize(QSize(26, 26))
        self.btn_settings.setFixedSize(42, 42)

        # LOGS BUTTON
        log_icon = qta.icon('mdi6.information-slab-circle', color=ACCENT_COLOR)
        self.btn_logs = QPushButton("")
        self.btn_logs.setToolTip("Logs")
        self.btn_logs.setIcon(log_icon)
        self.btn_logs.setCheckable(True)
        self.btn_logs.clicked.connect(lambda: self.switch_page(2))
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
        self.input_container_frame.setStyleSheet("""
            QFrame {
                color: #e1e2e8;
                background-color: transparent;
                border: 1px solid #8d9199;
                border-radius: 28px;
                font-size: 14px;
                selection-color: white;
            }
        """)
        
        # SEARCH BAR - TEXT INPUT
        self.search_input_vertical_padding = 12
        self.search_input = QTextEdit()
        self.search_input.setPlaceholderText("Type to search")
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
        self.image_list.setGridSize(QSize(200, 240))
        self.image_list.itemClicked.connect(self.open_results_dialog_from_list)
        self.image_list.setStyleSheet(f"""
            QListWidget {{ background-color: {BG_DARK}; border: none; outline: 0; selection-background-color: {BG_DARK}; padding-left: 35px; }}
            QListWidget::item {{ border: none; color: {TEXT_MAIN}; padding: 20px; border-radius: 0px;}}
            QListWidget::item:selected {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; border: none; outline: none;}}
            QListWidget::item:hover {{ background-color: {BG_LIGHT}; color: {TEXT_MAIN}; border: none; outline: none; }}
            QListWidget::item:selected:hover {{ background-color: {BG_LIGHT}; border: none; outline: none; }}
            QListWidget::item:pressed {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; border: none; outline: none; }}
        """)

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
        # Add it to the main search page
        search_layout.addWidget(self.results_tabs, 1)
        
    # SETTINGS PAGE
        self.page_settings = QWidget()
        # Use a VBox for the main page
        settings_main_layout = QVBoxLayout(self.page_settings)
        settings_main_layout.setContentsMargins(0, 0, 0, 0)

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
        
        # Configure content inside the scroll area
        scroll_content = QWidget()
        self.settings_layout = QVBoxLayout(scroll_content)
        self.settings_layout.setContentsMargins(40, 40, 40, 40)
        self.settings_layout.setSpacing(10)
        self.settings_layout.setAlignment(Qt.AlignTop)

    # SETTINGS SECTION 1: LIVE CONTROLS (No Restart Required)
        self.add_settings_header("Live Controls")

        # A. Model Toggles
        self.btn_ocr_toggle = self.add_live_setting_row("OCR Engine", "Load/Unload Windows OCR", 
                                  lambda: self.toggle_model('ocr'), color=OUTLINE)
        self.btn_embed_toggle = self.add_live_setting_row("Embeddings", "Load/Unload Embedding Models", 
                                  lambda: self.toggle_model('embed'), color=OUTLINE)
        self.btn_llm_toggle = self.add_live_setting_row("Local LLM", "Load/Unload Chat Model, enables AI Insights",
                                  lambda: self.toggle_model('llm'), color=OUTLINE)
        self.btn_screenshotter_toggle = self.add_live_setting_row("Screen Capture", f"Start/Stop taking screenshots every {self.config.get('screenshot_interval', 'N')} seconds, deleted after {self.config.get('delete_screenshots_after', 'N')} days", 
                                  lambda: self.toggle_model('screenshotter'), color=OUTLINE)

        self.add_live_setting_row("Data Folder", "Manage all created user data", 
                                  lambda: os.startfile(DATA_DIR), color=OUTLINE)
        # B. External Auth
        self.add_live_setting_row("Google Drive", "Reauthorize connection", 
                                  lambda: self.reauthorize_drive(), color=OUTLINE)
        # C. Database Actions
        self.add_live_setting_row("Retry Tasks", "Set all 'FAILED' tasks back to 'PENDING'", 
                                  lambda: self.run_db_action('retry_failed'), color="#d5b462")
        self.add_live_setting_row("Reset OCR Data", "Delete all OCR text & re-queue images", 
                                  lambda: self.run_db_action('reset_service', ['OCR']), color="#e06c75")
        self.add_live_setting_row("Reset Embeddings", "Delete all vectors & re-queue all files", 
                                  lambda: self.run_db_action('reset_service', ['EMBED', 'EMBED_LLM']), color="#e06c75")
        self.add_live_setting_row("Reset LLM Data", "Delete AI analysis & re-queue all files", 
                                  lambda: self.run_db_action('reset_service', ['LLM']), color="#e06c75")
        self.settings_layout.addSpacing(30)  # Space between sections

    # SETTINGS SECTION 2: CONFIGURATION (Restart Required)
        self.add_settings_header("System Configuration (Restart Required)")
        self.config_widgets = {} # To store inputs for saving
        # Iterate over config keys to create rows
        # Filter this list to hide internal keys!
        ignored_keys = ['quality_weight'] 
        for key, value in self.config.items():
            if key not in ignored_keys:
                self.add_config_row(key, value)

        self.settings_layout.addSpacing(20)
        
        # Large Save Button
        btn_save = QPushButton("Save Configuration")
        btn_save.setFixedHeight(45)
        btn_save.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_DARK}; color: {ACCENT_COLOR}; border-radius: 6px; border: 1px solid {ACCENT_COLOR}; font-weight: bold; }}
            QPushButton:hover {{ background-color: {ACCENT_COLOR}; color: {BG_DARK}; }}
        """)
        btn_save.clicked.connect(self.save_config)
        self.settings_layout.addWidget(btn_save)

        scroll.setWidget(scroll_content)
        settings_main_layout.addWidget(scroll)

    # LOGGING PAGE
        self.page_logs = QWidget()
        logs_layout = QVBoxLayout(self.page_logs)
        logs_layout.setContentsMargins(0, 0, 0, 0)
        
        self.log_output = QTextEdit()
        self.log_output.setReadOnly(True)
        self.log_output.document().setMaximumBlockCount(1000)  # Only show last 1000 messages
        self.log_output.setFont(QFont("Monospace", 9))
        self.log_output.setStyleSheet("QTextEdit { background-color: #111; color: #ccc; border: none; selection-color: white; }")  # selection-background-color: blue; 
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
        self.update_button_states()

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

    def eventFilter(self, obj, event):
        """Automatically found and installed by 'QApplication.instance().installEventFilter(self)'"""
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

    def reauthorize_drive(self):
        logger.info("Reauthorizing Google Drive...")
        def reauth_worker():
            token_path = Path("token.json")
            if token_path.exists():
                os.remove(token_path)
            try:
                self.drive_service = get_drive_service(self.config)
            except Exception as e:
                logger.error(f"[ERROR] Failed to get Drive service: {e}")
                self.drive_service = None
        threading.Thread(target=reauth_worker, daemon=True).start()

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
        dialog = AdvancedSearchDialog(self.search_filter, self.negative_filter, self)
        if dialog.exec():
            # Retrieve new state
            self.search_filter = dialog.folder_path
            self.negative_filter = dialog.negative_query
            # Update Button Visuals
            self.update_filter_icon()

    def update_filter_icon(self):
        """Updates the filter button to show active/inactive state."""
        has_filter = (self.search_filter is not None) or (self.negative_filter is not None)
        if has_filter:
            # Make it a little green
            self.btn_filter.setIcon(qta.icon('mdi.filter-variant', color=ACCENT_COLOR))
            # Build a helpful tooltip
            tips = []
            if self.search_filter: tips.append(f"Folder: {Path(self.search_filter).name}")
            if self.negative_filter: tips.append(f"Exclude: {self.negative_filter}")
            self.btn_filter.setToolTip(" | ".join(tips))
        else:
            # Default State
            self.btn_filter.setIcon(self.filter_icon) # The original icon
            self.btn_filter.setToolTip("Filter Search")

    def run_search(self):
        """The main entry point to start a search operation. Handles UI updates, worker management, and information collection."""
        query = self.search_input.toPlainText().strip()
        # Easter Egg: a search can be done with just a negative filter.
        if not query and not self.attached_file_path and not self.negative_filter:
            # This gives user the ability to get a clear slate, optional feature might remove
            self.doc_table.setRowCount(0)
            self.image_list.clear()
            self.llm_output.clear()
            return
        # Initialize data class to coordinate critical search information
        searchfacts = SearchFacts(query=query, negative_query=self.negative_filter, attachment_path=self.attached_file_path)
        # 1. Stop previous worker if it's still running (prevents race conditions)
        if self.workers:
            for w in self.workers:
                if isinstance(w, SearchWorker) and w.isRunning():
                    w.stop()
        # 2. Clear UI immediately (Instant feedback)
        self.btn_send.setIcon(self.send_spin_icon)
        self.doc_table.setRowCount(0)
        self.image_list.clear()
        self.llm_output.clear()
        # 3. Initialize Streaming Worker - does the actual search
        worker = SearchWorker(self.search_engine, searchfacts, self.search_filter)
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
            list_item = QListWidgetItem(icon, Path(item['path']).name)
            list_item.setData(Qt.UserRole, item)
            self.image_list.addItem(list_item)            
        except Exception as e: 
            logger.error(f"Error displaying stream image: {e}")

    def start_rag_generation(self, searchfacts):
        # A. Stop any existing LLM generation
        if self.workers:
            for w in self.workers:
                if isinstance(w, LLMWorker) and w.isRunning():
                    w.stop()
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
    def on_model_toggle_done(self, key, success):
        """Updates the UI after model load/unload is complete. Also resumes pending tasks if loading succeeded."""
        # Refresh the tray menu text to reflect the new state
        self.update_tray_menu()
        self.update_button_states()
        # Resume pending tasks
        if success:
            # CHANGE: Map keys to LISTS of task types
            # 'embed' now wakes up both standard embedding tasks AND summary embedding tasks
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
        ocr_loaded = self.models['ocr'].loaded
        embed_loaded = self.models['text'].loaded
        llm_loaded = self.models.get('llm') and self.models['llm'].loaded
        screenshotter_loaded = self.models.get('screenshotter') and self.models['screenshotter'].loaded
        # Set the text accordingly for the 4 buttons
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
        self.show()

    # --- CONFIG & DB LOGIC ---

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
            with open("config.json", "w") as f:
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

    def add_settings_header(self, text):
        lbl = QLabel(text)
        lbl.setFont(QFont("Segoe UI", 16, QFont.Bold))
        lbl.setStyleSheet(f"color: {ACCENT_COLOR}; margin-bottom: 10px;")
        self.settings_layout.addWidget(lbl)

    def add_live_setting_row(self, title, subtitle, callback, color):
        """Creates a row with Title, Subtitle, and an Action Button for the settings."""
        frame = QFrame()
        frame.setStyleSheet(f"background-color: {BG_LIGHT}; border-radius: 6px;")
        frame.setFixedHeight(60)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(15, 0, 15, 0)
        # Text
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        text_layout.setAlignment(Qt.AlignVCenter)
        t = QLabel(title)
        t.setFont(QFont("Segoe UI", 11, QFont.Bold))
        t.setStyleSheet("border: none; background: transparent;")
        s = QLabel(subtitle)
        s.setFont(QFont("Segoe UI", 9))
        s.setStyleSheet("color: #888; border: none; background: transparent;")
        text_layout.addWidget(t)
        text_layout.addWidget(s)
        # Button
        btn_text_color = "white"
        btn = QPushButton("Execute")
        btn.setFixedSize(90, 30)
        btn.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_LIGHT}; color: {btn_text_color}; border-radius: 4px; border: 1px solid {color}; }}
            QPushButton:hover {{ background-color: {color}; }}
        """)
        btn.clicked.connect(callback)
        # Assemble
        layout.addLayout(text_layout)
        layout.addStretch()
        layout.addWidget(btn)
        self.settings_layout.addWidget(frame)
        return btn  # So that the text can be updated later

    def add_config_row(self, key, value):
        """Creates a row with Key Label and an Input Field based on config.json"""
        frame = QFrame()
        frame.setStyleSheet(f"background-color: {BG_LIGHT}; border-radius: 6px;")
        frame.setFixedHeight(50)
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(15, 0, 15, 0)
        # Label (Clean up the key name: 'ocr_backend' -> 'OCR Backend')
        clean_name = key.replace("_", " ").title()
        lbl = QLabel(clean_name)
        lbl.setFont(QFont("Segoe UI", 11))
        lbl.setStyleSheet("border: none; background: transparent;")
        # Input
        inp = QLineEdit(str(value))
        inp.setAlignment(Qt.AlignRight)
        inp.setStyleSheet(f"""
            background-color: {BG_INPUT}; 
            border: 0px solid {OUTLINE}; 
            border-radius: 8px; 
            color: {ACCENT_COLOR};
            padding: 4px;
        """)
        inp.setFixedWidth(500)
        # Store for saving later
        self.config_widgets[key] = inp
        # Assemble
        layout.addWidget(lbl)
        layout.addStretch()
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
        if self.models.get('llm') and self.models['llm'].loaded:
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