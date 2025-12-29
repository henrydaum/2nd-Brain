import os
from pathlib import Path
import logging
import sys
import json
import threading

# Setup Logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')
logging.getLogger("AsyncWebsocketHandler").setLevel(logging.ERROR)
logger = logging.getLogger("Main")

# BASE_DIR for immutable core information and DATA_DIR for mutable data
BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv('LOCALAPPDATA')) / "2nd Brain"
DATA_DIR.mkdir(parents=True, exist_ok=True)

def load_config(file_path):
    """Loads configuration from a JSON file, creating a default one if missing."""
    
    # 1. Define your default settings
    DEFAULT_CONFIG = {
        "sync_directories": [
            "Z:\\My Drive", 
            str(DATA_DIR / "Screenshots")
        ],
        "batch_size": 16,
        "chunk_size": 1024,
        "chunk_overlap": 64,
        "flush_timeout": 5.0,
        "max_workers": 6,
        "ocr_backend": "Windows",
        "embed_backend": "Sentence Transformers",
        "embed_text_model_name": "BAAI/bge-small-en-v1.5",
        "embed_image_model_name": "clip-ViT-B-32",
        "llm_backend": "LM Studio",
        "llm_model_name": "gemma-3-4b-it@q4_k_s",
        "use_drive": True,
        "num_results": 30,
        "text_extensions": [".txt", ".md", ".pdf", ".docx", ".gdoc"],
        "image_extensions": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".ico"],
        "embed_use_cuda": True,
        "screenshot_interval": 15,
        "screenshot_folder": "Screenshots",
        "screenshot_delete_after": 9
    }

    # 2. Check if file exists
    if not os.path.exists(file_path):
        print(f"Config file not found. Creating default at: {file_path}")
        try:
            with open(file_path, 'w') as config_file:
                json.dump(DEFAULT_CONFIG, config_file, indent=4)
        except OSError as e:
            print(f"Error creating config file: {e}")
            return DEFAULT_CONFIG # Fallback to using defaults in memory

    # 3. Load the file (now guaranteed to exist)
    try:
        with open(file_path, 'r') as config_file:
            return json.load(config_file)
    except json.JSONDecodeError:
        print(f"Error: {file_path} is corrupted. Loading defaults.")
        return DEFAULT_CONFIG

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
        models['image'] = SentenceTransformerEmbedder(config['embed_image_model_name'], config)
        models['text'] = SentenceTransformerEmbedder(config['embed_text_model_name'], config)
    # LLM
    logger.info("Setting up LLM")
    from services.llmClass import LMStudioLLM, OpenAILLM
    if config['llm_backend'] == "LM Studio":
        models['llm'] = LMStudioLLM(config['llm_model_name'])
    elif config['llm_backend'] == "OpenAI":
        import keyring
        api_key = keyring.get_password("SecondBrain", "OPENAI_API_KEY")
        if not api_key:
            api_key = os.environ.get("OPENAI_API_KEY")
            logger.info("Got OpenAI API Key from environmental variable.")
        else:
            logger.info("Got OpenAI API Key from keyring.")
        models['llm'] = OpenAILLM(config['llm_model_name'], api_key)
    # Screenshotter
    logger.info("Setting up Screenshotter")
    from services.screenshotterClass import Screenshotter
    models['screenshotter'] = Screenshotter(config)
    # Done.
    return models

def backend_setup():
    # Local Imports
    from database import Database
    from orchestrator import Orchestrator
    from watcher import FileWatcherService
    from search import SearchEngine

    config = load_config(DATA_DIR / "config.json")
    db_path = Path(DATA_DIR / "Database/2nd_brain.db")
    db_path.parent.mkdir(parents=True, exist_ok=True)

    models = initialize_models(config)
    db = Database(db_path)
    orchestrator = Orchestrator(db, models, config)
    watcher = FileWatcherService(orchestrator, config)
    search_engine = SearchEngine(db, models, config)

    orch_thread = threading.Thread(target=orchestrator.start, daemon=True)
    orch_thread.start()
    watcher_thread = threading.Thread(target=watcher.start, daemon=True)
    watcher_thread.start()

    return orchestrator, watcher, search_engine, models, config

def main():
    logger.info("--- Starting Second Brain (PySide6) ---")
    from gui import MainWindow
    from PySide6.QtWidgets import QApplication
    
    # 1. Setup PySide6 Application
    app = QApplication(sys.argv)
    
    # Prevent the app from quitting when the window is closed (Key for Tray apps!)
    app.setQuitOnLastWindowClosed(False)

    # 3. Launch GUI
    # We pass 'models' directly so the GUI can toggle them
    window = MainWindow()
    window.start()

    # 5. Run Event Loop
    exit_code = app.exec()
    
    # 6. Clean Shutdown
    logger.info("Shutdown sequence initiated...")
    window.watcher.stop()
    window.orchestrator.stop()
    for key, model in window.models.items():
        model.unload()
    logger.info("Goodbye.")
    sys.exit(exit_code)

if __name__ == "__main__":
    main()