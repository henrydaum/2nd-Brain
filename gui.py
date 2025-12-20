import os
from pathlib import Path
import sys
import logging
import time
import threading
import subprocess
import json
import ast
from collections import deque
# Internal
from guiWorkers import SearchWorker, StatsWorker, ModelToggleWorker, DatabaseActionWorker, record_search_history
from Parsers import get_drive_service
# Qt
from PySide6.QtWidgets import (
    QApplication, QMainWindow, QWidget, QVBoxLayout, QHBoxLayout,
    QTextEdit, QPushButton, QStackedWidget, QTableWidget, QTableWidgetItem,
    QListWidget, QListWidgetItem, QSystemTrayIcon, QMenu, QHeaderView,
    QLabel, QFrame, QAbstractItemView, QTabWidget, QStatusBar, QLineEdit, QScrollArea, QDialog, QTextBrowser, QFileDialog
)
from PySide6.QtCore import Qt, QSize, Signal, Slot, QEvent
from PySide6.QtGui import QIcon, QPixmap, QFont, QColor, QBrush, QAction, QImage
import qtawesome as qta

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
BORDER_RADIUS= "6px"
OUTLINE      = "#3e4451"

# --- LOGGING HANDLER ---
class GuiLogHandler(logging.Handler):
    """A custom logging handler that calls a function with the log record."""
    
    def __init__(self, log_display_callback):
        super().__init__()
        self.log_display_callback = log_display_callback # This will be MainWindow.display_log_message
        formatter = logging.Formatter('%(asctime)s - %(levelname)s - %(name)s - %(message)s', datefmt='%H:%M:%S')
        self.setFormatter(formatter)
        
    def emit(self, record):
        msg = self.format(record)
        # Call the MainWindow method, which uses the signal for thread safety
        self.log_display_callback(msg)

class ResultDetailsDialog(QDialog):
    def __init__(self, item_data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Result Details")
        self.setFixedSize(500, 375)
        self.path = item_data.get('path', 'Unknown')
        
        # --- STYLES ---
        # Reuse your main app styling constants here for consistency
        BG_DARK = "#1e2227"
        BG_LIGHT = "#282c34"
        TEXT_MAIN = "#abb2bf"
        ACCENT = "#cbe3a7"
        
        self.setStyleSheet(f"""
            QDialog {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; }}
            QLabel {{ color: {TEXT_MAIN}; font-size: 14px; }}
            QTextBrowser {{ background-color: {BG_LIGHT}; border: none; padding: 10px; color: {TEXT_MAIN}; font-size: 13px; }}
            QPushButton {{ background-color: {BG_LIGHT}; color: white; border-radius: 4px; padding: 8px; border: 1px solid #3e4451; }}
            QPushButton:hover {{ background-color: #3e4451; }}
        """)

        layout = QVBoxLayout(self)
        layout.setSpacing(15)
        layout.setContentsMargins(20, 20, 20, 20)

        # 1. Header (Filename)
        lbl_name = QLabel(Path(self.path).name)
        lbl_name.setFont(QFont("Segoe UI", 16, QFont.Bold))
        lbl_name.setStyleSheet(f"color: {ACCENT};")
        layout.addWidget(lbl_name)

        # 2. Metadata Row (Score | Type)
        meta_layout = QHBoxLayout()
        score = item_data.get('score', 0.0)
        m_type = item_data.get('match_type', 'Unknown').upper()
        
        lbl_meta = QLabel(f"<b>SCORE:</b> {score:.2f}   |   <b>TYPE:</b> {m_type}")
        lbl_meta.setStyleSheet("color: #888;")
        meta_layout.addWidget(lbl_meta)
        meta_layout.addStretch()
        layout.addLayout(meta_layout)

        # 3. Content Area (The Text)
        self.text_browser = QTextBrowser()
        content = item_data.get('content', 'No preview text available.')
        self.text_browser.setText(content)
        layout.addWidget(self.text_browser)

        # btn.setFixedSize(90, 30)

        # 4. Buttons (Open File | Close)
        btn_layout = QHBoxLayout()
        btn_text_color = "white"
        
        # Copy Path Button
        color = OUTLINE
        btn_copy = QPushButton("Copy Path")
        btn_copy.setCursor(Qt.PointingHandCursor)
        btn_copy.clicked.connect(lambda: QApplication.clipboard().setText(self.path))
        btn_copy.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_DARK}; color: {btn_text_color}; border-radius: 4px; border: 1px solid {color}; }}
            QPushButton:hover {{ background-color: {color}; }}
        """)

        # Reveal in Explorer Button
        color = OUTLINE
        btn_reveal = QPushButton("Show Location")
        btn_reveal.setCursor(Qt.PointingHandCursor)
        btn_reveal.clicked.connect(self.reveal_in_explorer)
        btn_reveal.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_DARK}; color: {btn_text_color}; border-radius: 4px; border: 1px solid {color}; }}
            QPushButton:hover {{ background-color: {color}; }}
        """)

        # Open File
        color = OUTLINE
        btn_open = QPushButton("Open File")
        btn_open.setCursor(Qt.PointingHandCursor)
        btn_open.clicked.connect(self.open_file)
        btn_open.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_DARK}; color: {btn_text_color}; border-radius: 4px; border: 1px solid {color}; }}
            QPushButton:hover {{ background-color: {color}; }}
        """)
        
        # Close
        color = ACCENT_COLOR
        btn_close = QPushButton("Close")
        btn_close.setCursor(Qt.PointingHandCursor)
        btn_close.clicked.connect(self.accept)
        btn_close.setStyleSheet(f"""
            QPushButton {{ background-color: {BG_DARK}; color: {ACCENT_COLOR}; border-radius: 4px; border: 1px solid {color}; }}
            QPushButton:hover {{ background-color: {color}; color: {BG_DARK}; }}
        """)
        
        # Add to layout
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

class MainWindow(QMainWindow):
    # Define the signal at the QObject class level
    log_signal = Signal(str)

    def __init__(self, search_engine, orchestrator, models, config):
        super().__init__()
        self.search_engine = search_engine
        self.orchestrator = orchestrator
        self.models = models
        self.config = config
        self.drive_service = get_drive_service(self.config)
        self.workers = []
        self.search_filter = None  # None means "Search Everything"
        self.attached_file_path = None
        
        self.setWindowTitle("Second Brain")
        self.resize(900, 600)
        self.icon_path = str(BASE_DIR / "icon.ico")
        self.setWindowIcon(QIcon(self.icon_path))
        
        self.setup_styles()
        self.setup_ui()
        self.setup_tray()

        self.konami_code = [
            Qt.Key_Up.value, Qt.Key_Up.value, Qt.Key_Down.value, Qt.Key_Down.value,
            Qt.Key_Left.value, Qt.Key_Right.value, Qt.Key_Left.value, Qt.Key_Right.value,
            Qt.Key_B.value, Qt.Key_A.value
        ]
        self.key_history = deque(maxlen=len(self.konami_code))
        self.last_key_time = 0  # Cooldown tracking
        QApplication.instance().installEventFilter(self)

        # --- Configure Logging ---
        self.log_signal.connect(self.display_log_message_safe)
        self.log_handler = GuiLogHandler(self.log_signal.emit)
        
        # Get the root logger and add the custom handler
        root_logger = logging.getLogger()
        root_logger.addHandler(self.log_handler)
        root_logger.setLevel(logging.INFO)
        
        # Start Stats Polling
        if hasattr(self.search_engine, 'db'):
            self.stats_thread = StatsWorker(self.search_engine.db)
            self.stats_thread.stats_updated.connect(self.update_status_bar)
            self.stats_thread.start()

    def setup_styles(self):
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
        
        side_layout.addWidget(self.btn_search)
        side_layout.addStretch()
        side_layout.addWidget(self.btn_settings)
        side_layout.addWidget(self.btn_logs)
        main_layout.addWidget(self.sidebar)

        # --- STACK ---
        self.stack = QStackedWidget()
        
    # SEARCH PAGE
        self.page_search = QWidget()
        search_layout = QVBoxLayout(self.page_search)
        search_layout.setContentsMargins(0, 0, 0, 0)
        search_layout.setSpacing(15)

        # INPUT WRAPPER - for spacing
        self.input_wrapper = QWidget()
        input_wrapper_layout = QHBoxLayout(self.input_wrapper)
        
        # 1. APPLY THE DESIRED MARGINS/PADDING HERE (e.g., 15px all around)
        # This margin applies *around* the input_container_frame inside the wrapper.
        input_wrapper_layout.setContentsMargins(70, 40, 70, 10) # L, T, R, B (set bottom to 0)
        input_wrapper_layout.setSpacing(0)

        # INPUT CONTAINER
        self.input_container_frame = QFrame()
        input_container_layout = QHBoxLayout(self.input_container_frame)
        input_container_layout.setContentsMargins(4, 5, 7, 0) # L, T, R, B
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
        """)  # selection-background-color: blue; 
        
        # SEARCH BAR - TEXT INPUT
        self.search_input_vertical_padding = 12
        self.search_input = QTextEdit()
        self.search_input.setPlaceholderText("Type to search")
        doc_height = int(self.search_input.document().size().height())
        self.search_input_min_height = doc_height
        self.search_input_max_height = (doc_height * 7)
        self.search_input.textChanged.connect(self.adjust_search_input_height)
        self.search_input.setFixedHeight(doc_height + (self.search_input_vertical_padding * 2) + 3)  # adjust +4 so it doesn't wiggle on start
        self.search_input.setFixedWidth(self.search_input.width())
        self.search_input.setStyleSheet("border: none;")
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
        """)  # selection-background-color: blue;

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
        self.btn_filter.clicked.connect(self.handle_filter_folder)
        self.btn_filter_container = QWidget()
        self.btn_filter_container.setStyleSheet("""background-color: transparent;""")
        btn_filter_layout = QVBoxLayout(self.btn_filter_container)
        # This layout adds the 8px lift
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
        # This layout adds the 8px lift
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
        # This layout adds the 8px lift
        btn_send_layout.setContentsMargins(0, 1.5, 0, 0) # L, T, R, Bottom
        btn_send_layout.addWidget(self.btn_send)

        # ASSEMBLE INPUT CONTAINER
        input_container_layout.addWidget(self.search_input)
        input_container_layout.addWidget(self.btn_filter_container, 0, Qt.AlignmentFlag.AlignTop)
        input_container_layout.addWidget(self.btn_attach_container, 0, Qt.AlignmentFlag.AlignTop)
        input_container_layout.addWidget(self.btn_send_container, 0, Qt.AlignmentFlag.AlignTop)
        input_wrapper_layout.addStretch()
        input_wrapper_layout.addWidget(self.input_container_frame)
        input_wrapper_layout.addStretch()
        search_layout.addWidget(self.input_wrapper, 0)
        
        # RESULTS AREA
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
        self.doc_table.itemClicked.connect(self.open_file_from_table)
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
        # --- PADDING WRAPPER FOR DOCUMENTS ---
        self.tab_doc_container = QWidget()
        doc_layout = QVBoxLayout(self.tab_doc_container)
        doc_layout.setContentsMargins(0, 0, 0, 0)  # L, T, R, Bottom
        doc_layout.addWidget(self.doc_table)
        
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
        self.image_list.itemClicked.connect(self.open_file_from_list)
        self.image_list.setStyleSheet(f"""
            QListWidget {{ background-color: {BG_DARK}; border: none; outline: 0; selection-background-color: {BG_DARK}; }}
            QListWidget::item {{ border: none; color: {TEXT_MAIN}; padding: 20px; border-radius: 0px;}}
            QListWidget::item:selected {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; border: none; outline: none;}}
            QListWidget::item:hover {{ background-color: {BG_LIGHT}; color: {TEXT_MAIN}; border: none; outline: none; }}
            QListWidget::item:selected:hover {{ background-color: {BG_LIGHT}; border: none; outline: none; }}
            QListWidget::item:pressed {{ background-color: {BG_DARK}; color: {TEXT_MAIN}; border: none; outline: none; }}
        """)
        # --- PADDING WRAPPER FOR IMAGES ---
        self.tab_img_container = QWidget()
        img_layout = QVBoxLayout(self.tab_img_container)
        img_layout.setContentsMargins(35, 0, 0, 0)  # L, T, R, Bottom
        img_layout.addWidget(self.image_list)

        # ADD THE WRAPPERS TO THE TABS (Instead of the raw widgets)
        self.results_tabs.addTab(self.tab_doc_container, "Documents")
        self.results_tabs.addTab(self.tab_img_container, "Images")
        search_layout.addWidget(self.results_tabs, 1)
        
    # SETTINGS PAGE
        self.page_settings = QWidget()
        # We use a VBox for the main page
        settings_main_layout = QVBoxLayout(self.page_settings)
        settings_main_layout.setContentsMargins(0, 0, 0, 0)

        # Scroll Area (In case you have many config options)
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
        self.settings_layout = QVBoxLayout(scroll_content)
        self.settings_layout.setContentsMargins(40, 40, 40, 40)
        self.settings_layout.setSpacing(10)
        self.settings_layout.setAlignment(Qt.AlignTop)

        # --- SECTION 1: LIVE CONTROLS (No Restart) ---
        self.add_settings_header("Live Controls")

        # A. Model Toggles (Reusing your logic)
        self.btn_ocr_toggle = self.add_live_setting_row("OCR Engine", "Load/Unload Windows OCR", 
                                  lambda: self.toggle_model('ocr'), color=OUTLINE)

        self.btn_embed_toggle = self.add_live_setting_row("Embeddings", "Load/Unload Embedding Models", 
                                  lambda: self.toggle_model('embed'), color=OUTLINE)

        self.btn_llm_toggle = self.add_live_setting_row("Local LLM", "Load/Unload Chat Model", 
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
                                  lambda: self.run_db_action('reset_service', 'OCR'), color="#e06c75")
        self.add_live_setting_row("Reset Embeddings", "Delete all vectors & re-queue all files", 
                                  lambda: self.run_db_action('reset_service', 'EMBED'), color="#e06c75")
        self.add_live_setting_row("Reset LLM Data", "Delete AI analysis & re-queue all files", 
                                  lambda: self.run_db_action('reset_service', 'LLM'), color="#e06c75")

        self.settings_layout.addSpacing(30)

        # --- SECTION 2: CONFIGURATION (Restart Required) ---
        self.add_settings_header("System Configuration (Restart Required)")

        self.config_widgets = {} # To store inputs for saving
        
        # Iterate over config keys to create rows
        # You can filter this list if you want to hide internal keys
        ignored_keys = ['quality_weight'] 
        for key, value in self.config.items():
            if key not in ignored_keys:
                self.add_config_row(key, value)

        self.settings_layout.addSpacing(20)
        
        # Save Button
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
        # Helper to format each section
        def fmt_stat(name, model_key, data):
            # 1. Status Icon
            is_loaded = self.models.get(model_key) and self.models[model_key].loaded
            icon = "✦" if is_loaded else "✧"
            
            # 2. Safe Data Extraction (Handle potential missing keys)
            p = data.get("PENDING", 0)
            d = data.get("DONE", 0)
            f = data.get("FAILED", 0)
            rows = data.get("DB_ROWS", 0)
            
            return f"[{name} {icon}] {100*((d+f)/(d+f+p)):.2f}%"  # Percentage completion

        # Build the sections
        # Note: Map 'EMBED' stat to 'text' model key
        s_ocr = fmt_stat("OCR", "ocr", stats.get("OCR", {}))
        s_emb = fmt_stat("EMBED", "text", stats.get("EMBED", {}))
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

    def handle_attach(self):
        """
        Toggle: 
        - If NO attachment -> Open File Picker -> Set 'X' icon.
        - If YES attachment -> Clear it -> Reset 'Paperclip' icon.
        """
        # --- CASE 1: REMOVE ATTACHMENT ---
        if self.attached_file_path:
            self.attached_file_path = None
            self.btn_attach.setToolTip("Attach File")
            
            # Restore the original Paperclip Icon
            # (assuming default icon color is gray/TEXT_MAIN)
            self.btn_attach.setIcon(qta.icon('ph.paperclip-bold')) 
            logger.info("Attachment removed by user.")
            return

        # --- CASE 2: ADD ATTACHMENT ---
        # 1. Prepare Filters
        text_exts = self.config.get('text_extensions', [])
        img_exts = self.config.get('image_extensions', [])
        all_exts = text_exts + img_exts

        def fmt(ext_list):
            return " ".join([f"*{ext}" for ext in ext_list])

        filters = [
            f"All Supported Files ({fmt(all_exts)})",
            f"Text Documents ({fmt(text_exts)})",
            f"Images ({fmt(img_exts)})",
            "All Files (*)"
        ]

        # 2. Open Dialog
        path, _ = QFileDialog.getOpenFileName(
            self, 
            "Attach File to Search", 
            "", 
            ";;".join(filters)
        )

        # 3. Handle Selection
        if path:
            self.attached_file_path = path
            filename = Path(path).name
            
            # Update Tooltip to indicate clicking will "Remove"
            self.btn_attach.setToolTip(f"Remove attachment: {filename}")
            
            # Change Icon to a Red 'X'
            # using 'mdi.close' or 'mdi.window-close'
            self.btn_attach.setIcon(qta.icon('mdi.close')) 
            
            logger.info(f"Attached file: {path}")

    def handle_filter_folder(self):
        """
        If no folder is selected: Opens directory picker.
        If folder IS selected: Clears the filter.
        """
        if self.search_filter is None:
            # State 1: Pick a Folder
            folder = QFileDialog.getExistingDirectory(self, "Select Folder to Search In")
            
            if folder: # If user didn't cancel
                self.search_filter = folder
                
                # Change UI to "Active Filter" state
                # Use a "Close/X" icon
                remove_icon = qta.icon('mdi.filter-variant-remove')
                self.btn_filter.setIcon(remove_icon)
                self.btn_filter.setToolTip(f"Searching within: {folder}")
                
        else:
            # State 2: Clear the Filter
            self.search_filter = None
            
            # Reset UI to "Default" state
            self.btn_filter.setIcon(self.filter_icon)
            self.btn_filter.setToolTip("Searching all files")

    def run_search(self):
        query = self.search_input.toPlainText().strip()
        if not query: return

        # 0. Log the query to search history
        threading.Thread(target=record_search_history, args=(query,), daemon=True).start()
        
        # 1. Stop previous worker if it's still running (prevents race conditions)
        if self.workers:
            for w in self.workers:
                if isinstance(w, SearchWorker) and w.isRunning():
                    w.stop()
        
        # 2. Clear UI immediately (Instant feedback)
        self.btn_send.setIcon(self.send_spin_icon)
        self.doc_table.setRowCount(0)
        self.image_list.clear()

        # 3. Start Streaming Worker
        worker = SearchWorker(self.search_engine, query, self.search_filter)
        
        # Connect the split signals
        worker.text_ready.connect(self.on_text_ready)
        worker.image_stream.connect(self.on_image_stream)
        
        # Cleanup when totally done
        worker.finished.connect(lambda: self.btn_send.setIcon(self.send_icon))
        worker.finished.connect(lambda: self.cleanup_worker(worker))
        
        self.workers.append(worker)
        worker.start()

    @Slot(list)
    def on_text_ready(self, text_res):
        """Populates the text table all at once (since it's fast)"""
        self.doc_table.setRowCount(len(text_res))
        for row, item in enumerate(text_res):
            self.doc_table.setRowHeight(row, 60)
            
            score = QTableWidgetItem(f"{item['score']:.2f}")
            score.setForeground(QBrush(QColor(ACCENT_COLOR)))
            score.setTextAlignment(Qt.AlignCenter)
            self.doc_table.setItem(row, 0, score)

            name_text = Path(item['path']).stem
            path_text = str(item['path'])
            name_item = QTableWidgetItem("") 
            self.doc_table.setItem(row, 1, name_item)
            cell_widget = self.create_file_cell_widget(name_text, path_text)
            self.doc_table.setCellWidget(row, 1, cell_widget)
            
            type_ = QTableWidgetItem(item.get('match_type', 'Mix').upper())
            self.doc_table.setItem(row, 2, type_)
            path_item = QTableWidgetItem(path_text)
            path_item.setData(Qt.UserRole, item)
            self.doc_table.setItem(row, 3, path_item)

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

            icon = QIcon()
            icon.addPixmap(pixmap, QIcon.Mode.Normal)
            icon.addPixmap(pixmap, QIcon.Mode.Selected)
            list_item = QListWidgetItem(icon, Path(item['path']).name)
            list_item.setData(Qt.UserRole, item)
            self.image_list.addItem(list_item)
            
        except Exception as e: 
            logger.error(f"Error displaying stream image: {e}")

    # --- MODEL & TRAY LOGIC ---

    def toggle_model(self, key):
        current = False
        if key == 'ocr': current = self.models['ocr'].loaded
        elif key == 'embed': current = self.models['text'].loaded
        elif key == 'llm': current = self.models['llm'].loaded
        elif key == 'screenshotter': current = self.models['screenshotter'].loaded
        
        action = "unload" if current else "load"
        
        worker = ModelToggleWorker(self.models, key, action)
        worker.finished.connect(self.on_model_toggle_done)
        worker.finished.connect(lambda: self.cleanup_worker(worker))
        self.workers.append(worker)
        worker.start()

    @Slot(str, bool)
    def on_model_toggle_done(self, key, success):
        state = "Ready" if success else "Failed"
        # Refresh the tray menu text to reflect the new state
        self.update_tray_menu()
        self.update_button_states()
        # Resume pending tasks
        if success:
            # Map the GUI key ('ocr', 'embed', 'llm') to the DB Task Type
            key_map = {
                'ocr': 'OCR', 
                'embed': 'EMBED', 
                'llm': 'LLM'
            }
            # Get the uppercase DB type (e.g., 'OCR')
            task_type = key_map.get(key)
            
            if task_type:
                # Tell Orchestrator to scan DB for sleeping tasks of this type
                threading.Thread(target=self.orchestrator.resume_pending, args=(task_type,),  daemon=True).start()

    def setup_tray(self):
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
        
        show_action = QAction("Open", self)
        # Make it bold
        font = QFont()
        font.setBold(True)
        show_action.setFont(font)
        show_action.triggered.connect(self.show)

        # Screenshotting software ("Windows Recall")
        self.act_screenshot = QAction("Start Screen Capture", self)
        self.act_screenshot.triggered.connect(lambda: self.toggle_model('screenshotter'))
        
        # Model Actions
        self.act_ocr = QAction("Load OCR", self)
        self.act_ocr.triggered.connect(lambda: self.toggle_model('ocr'))
        
        self.act_embed = QAction("Load Embedders", self)
        self.act_embed.triggered.connect(lambda: self.toggle_model('embed'))
        
        self.act_llm = QAction("Load LLM", self)
        self.act_llm.triggered.connect(lambda: self.toggle_model('llm'))

        quit_action = QAction("Quit", self)
        # Make it bold
        show_action.setFont(font)
        quit_action.setFont(font)
        quit_action.triggered.connect(QApplication.quit)

        self.tray_menu.addAction(show_action)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.act_screenshot)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(self.act_ocr)
        self.tray_menu.addAction(self.act_embed)
        self.tray_menu.addAction(self.act_llm)
        self.tray_menu.addSeparator()
        self.tray_menu.addAction(quit_action)

        self.tray_icon.setContextMenu(self.tray_menu)
        self.tray_icon.activated.connect(self.on_tray_activated)
        self.tray_icon.show()
        
        # Initial State Check
        self.update_tray_menu()

    def update_tray_menu(self):
        """Updates the labels based on loaded state"""
        ocr_loaded = self.models['ocr'].loaded
        embed_loaded = self.models['text'].loaded
        llm_loaded = self.models.get('llm') and self.models['llm'].loaded
        screenshotter_loaded = self.models.get('screenshotter') and self.models['screenshotter'].loaded
        
        self.act_screenshot.setText("Stop Screen Capture" if screenshotter_loaded else "Start Screen Capture")

        self.act_ocr.setText("Unload OCR" if ocr_loaded else "Load OCR")
        self.act_embed.setText("Unload Embedders" if embed_loaded else "Load Embedders")
        self.act_llm.setText("Unload LLM" if llm_loaded else "Load LLM")

    def on_tray_activated(self, reason):
        if reason == QSystemTrayIcon.Trigger:
            if self.isVisible():
                self.hide()
            else:
                self.show()
                self.activateWindow()

    def closeEvent(self, event):
        if self.tray_icon.isVisible():
            self.hide()
            event.ignore()
        else:
            event.accept()

    def open_file_from_table(self, item):
        # The data is stored in Column 3 (the hidden path column)
        # Note: 'item' here is whichever cell was clicked. We need the item from col 3.
        row = item.row()
        path_item = self.doc_table.item(row, 3)
        data = path_item.data(Qt.UserRole)
        
        dialog = ResultDetailsDialog(data, self)
        dialog.exec()

    def open_file_from_list(self, item):
        # The data is stored directly in the item's UserRole
        data = item.data(Qt.UserRole)
        
        dialog = ResultDetailsDialog(data, self)
        dialog.exec()

    def start(self):
        self.show()

    # --- CONFIG & DB LOGIC ---

    def save_config(self):
        """Reads values from UI inputs and writes to config.json"""        
        # Update self.config from the UI fields we stored
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

        # Write to file
        try:
            with open("config.json", "w") as f:
                json.dump(self.config, f, indent=4)
            self.status_bar.showMessage("Configuration saved.", 5000)
        except Exception as e:
            self.status_bar.showMessage(f"Failed to save config: {e}", 5000)

    def run_db_action(self, action_type, service_key=None):
        """Runs the DB worker, with a confirmation for destructive actions."""
        
        # 1. Check if this is a 'Danger' action (Resetting service data)
        if action_type == 'reset_service':
            from PySide6.QtWidgets import QMessageBox
            
            # Create the 'Are you sure?' box
            msg_box = QMessageBox(self)
            msg_box.setWindowTitle("Confirm Data Reset")
            msg_box.setText(f"Are you sure you want to reset {service_key} data?")
            msg_box.setInformativeText("This will delete all processed records for this service and re-queue your files. This action cannot be undone.")
            msg_box.setIcon(QMessageBox.Warning)
            msg_box.setStandardButtons(QMessageBox.Yes | QMessageBox.Cancel)
            msg_box.setDefaultButton(QMessageBox.Cancel)
            
            # Show the box and capture the user's choice
            choice = msg_box.exec()
            
            if choice == QMessageBox.Cancel:
                return  # Exit early; do nothing

        # 2. Proceed with the worker if confirmed (or if it's not a danger action)
        worker = DatabaseActionWorker(self.search_engine.db, self.orchestrator, action_type, service_key)
        
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
        """Creates a row with Title, Subtitle, and an Action Button"""
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

        layout.addLayout(text_layout)
        layout.addStretch()
        layout.addWidget(btn)
        
        self.settings_layout.addWidget(frame)
        return btn

    def add_config_row(self, key, value):
        """Creates a row with Key Label and an Input Field"""
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

        if self.models.get('screenshotter') and self.models['screenshotter'].loaded:
            self.btn_screenshotter_toggle.setText("Stop")
        else:
            self.btn_screenshotter_toggle.setText("Start")

    def cleanup_worker(self, worker):
        if worker in self.workers:
            self.workers.remove(worker)
        worker.deleteLater() # clean up C++ resources