import os
from pathlib import Path
import logging
import sys
import json
import threading
# 3rd Party
from PySide6.QtWidgets import QApplication
# Local Imports
from database import Database
from orchestrator import Orchestrator
from watcher import FileWatcherService
from gui import MainWindow
from search import SearchEngine
from config import Config

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("AsyncWebsocketHandler").setLevel(logging.ERROR)
logger = logging.getLogger("Main")

# BASE_DIR for immutable core information and DATA_DIR for mutable data
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv('LOCALAPPDATA')) / "2nd Brain"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def initialize_models(config):
    models = {}
    # OCR
    logger.info("Setting up OCR")
    from services.ocrClass import WindowsOCR
    if config['ocr_backend'] == "Windows":
        models['ocr'] = WindowsOCR(config)
    # EMBED
    logger.info("Setting up Embedding Models")
    from services.embedClass import SentenceTransformerEmbedder
    if config['embed_backend'] == "Sentence Transformers":
        models['image'] = SentenceTransformerEmbedder(config['image_model_name'], config)
        models['text'] = SentenceTransformerEmbedder(config['text_model_name'], config)
    # LLM
    logger.info("Setting up LLM")
    from services.llmClass import LMStudioLLM, OpenAILLM
    if config['llm_backend'] == "LM Studio":
        models['llm'] = LMStudioLLM(config['lms_model_name'])
    elif config['llm_backend'] == "OpenAI":
        import keyring
        api_key = keyring.get_password("SecondBrain", "OPENAI_API_KEY")
        logger.info("Got OpenAI API Key from keyring.")
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY")
            logger.info("Got OpenAI API Key from environmental variable.")
        models['llm'] = OpenAILLM(config['openai_model_name'], api_key)
    # Screenshotter
    logger.info("Setting up Screenshotter")
    from services.screenshotterClass import Screenshotter
    models['screenshotter'] = Screenshotter(config)
    # Done.
    return models

def main():
    logger.info("--- Starting Second Brain (PySide6) ---")
    
    # 1. Setup PySide6 Application
    app = QApplication(sys.argv)
    
    # Prevent the app from quitting when the window is closed (Key for Tray apps!)
    app.setQuitOnLastWindowClosed(False)

    # 2. Configuration & Backend
    config = Config(DATA_DIR)
    db_path = Path(DATA_DIR / "Database/2nd_brain.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    models = initialize_models(config)
    db = Database(db_path)
    orchestrator = Orchestrator(db, models, config)
    watcher = FileWatcherService(orchestrator, config)
    search_engine = SearchEngine(db, models, config)

    # 3. Launch GUI
    # We pass 'models' directly so the GUI can toggle them
    window = MainWindow(search_engine, orchestrator, models, config)
    window.start() 

    # 4. Start Background Services
    orch_thread = threading.Thread(target=orchestrator.start, daemon=True)
    orch_thread.start()
    watcher_thread = threading.Thread(target=watcher.start, daemon=True)
    watcher_thread.start()

    # 5. Run Event Loop
    exit_code = app.exec()
    
    # 6. Clean Shutdown
    logger.info("Shutdown sequence initiated...")
    watcher.stop()
    orchestrator.stop()
    for key, model in models.items():
        model.unload()
    logger.info("Goodbye.")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()