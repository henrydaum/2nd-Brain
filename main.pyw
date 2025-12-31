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

CONFIG_DATA = {  # setting name: (display name, description, default value, gui object type)
    "sync_directories":        ("Sync Directories",             "A list of folders to sync.", ["Z:\\My Drive", str(DATA_DIR / "Screenshots")]),
    "text_extensions":         ("Text Extensions",              "A list of desired text extensions to sync, chosen out of these valid ones: ['.txt', '.md', '.pdf', '.docx', '.gdoc', '.rtf', '.pptx', '.csv', '.xlsx', '.xls', '.json', '.yaml', '.yml', '.xml', '.ini', '.toml', '.env', '.py', '.js', '.jsx', '.ts', '.tsx', '.html', '.css', '.c', '.cpp', '.h', '.java', '.cs', '.php', '.rb', '.go', '.rs', '.sql', '.sh', '.bat', '.ps1']", [".txt", ".md", ".pdf", ".docx", ".gdoc"]),
    "image_extensions":        ("Image Extensions",             "A list of desired image extensions to sync, chosen out of these valid ones: ['.png', '.jpg', '.jpeg', '.gif', '.webp', '.heic', '.heif', '.tif', '.tiff', '.bmp', '.ico']", [".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".ico"]),
    "ignored_folders":         ("Ignored Folder Names",         "Items in these folders will not be synced.", ["__pycache__", ".venv", ".vscode", ".tmp.driveupload", ".tmp.drivedownload", ".git"]),
    "skip_hidden_folders":     ("Skip Hidden Folders",          "Whether or not to skip hidden folders (folders that start with a period, e.g. '.git')", True),
    "use_drive":               ("Use Google Drive",             "Whether or not to sync .gdoc files. Requires that credentials.json, from Google Cloud, is in the local AppData Folder.", False),
    "num_results":             ("Maximum Results",              "'Document' and 'Image' results can each show a maximum of this many results.", 50),
    "batch_size":              ("Batch Size",                   "How many files to process at once for EMBED tasks, as well as how many database entries to delete at once.", 16),
    "chunk_size":              ("Chunk Size",                   "Embedding splits text documents into chunks that are each this many tokens.", 256),
    "chunk_overlap":           ("Chunk Overlap",                "How many tokens to share between adjacent chunks.", 16),
    "flush_timeout":           ("Flush Timeout",                "EMBED and DELETE tasks are processed in batches, however, if there are no new tasks after this many seconds, all the remaining tasks will be processed in a batch.", 5.0),
    "max_workers":             ("Number of Workers",            "How many tasks to process simultaneously. Using more workers is faster but requires more compute.", 6),
    "ocr_backend":             ("OCR Backend",                  "Source for OCR. Only option (right now): 'Windows'.", "Windows"),
    "embed_backend":           ("Embed Backend",                "Source for embedding models. Only option: 'Sentence Transformers' (from Hugging Face).", "Sentence Transformers"),
    "embed_text_model_name":   ("Text Embedder Model Name",     "Good options include: 'BAAI/bge-small-en-v1.5', 'BAAI/bge-large-en-v1.5', and 'BAAI/bge-m3'.", "BAAI/bge-small-en-v1.5"),
    "embed_image_model_name":  ("Image Embedder Model Name.",    "Good options include: 'clip-ViT-B-32', 'clip-ViT-B-16', and 'clip-ViT-L-14'.", "clip-ViT-B-32"),
    "embed_use_cuda":          ("GPU Acceleration",             "Whether or not to use NVIDIA CUDA + GPU for embedding tasks (if available). Provides a considerable speedup.", True),
    "llm_backend":             ("LLM Backend",                  "Source for the LLM. Options: 'OpenAI' or 'LM Studio'.", "LM Studio"),
    "llm_model_name":          ("LLM Name",                     "Name for a model to use from the LLM backend.", "gemma-3-4b-it"),
    "llm_context_length":      ("Context Length",               "The maximum number of tokens the LLM can process at once. Depends on the LLM backend and model.", 4096),
    "llm_temperature":         ("Temperature",                  "Degree of randomness in LLM responses.", 0.7),
    "llm_system_prompt":       ("System Prompt",                "Basic instructions given to LLM for AI Insights.", "Summarize all of the results and cite your sources."),
    "screenshot_interval":     ("Screenshot Interval",          "While Screen Capture is active, screenshots are taken this many seconds apart.", 15),
    "screenshot_folder":       ("Screenshot Folder",            "If left blank, screenshots are deposited to a 'Screenshots' folder within the local AppData folder.", ""),
    "screenshot_delete_after": ("Screenshot Lifespan",          "Screenshots are deleted from the folder after this many days (while Screen Capture is active).", 14),
}

def load_config(file_path):
    """Loads configuration from a JSON file, creating a default one if missing."""
    
    # 1. Define default settings
    DEFAULT_CONFIG = {}
    for key, value in CONFIG_DATA.items():
        DEFAULT_CONFIG[key] = value[2]

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
    logger.info("Initializing OCR")
    from services.ocrClass import WindowsOCR
    if config['ocr_backend'] == "Windows":
        models['ocr'] = WindowsOCR(config)
    # EMBED
    logger.info("Initializing Embedding Models")
    from services.embedClass import SentenceTransformerEmbedder
    if config['embed_backend'] == "Sentence Transformers":
        models['image'] = SentenceTransformerEmbedder(config['embed_image_model_name'], config)
        models['text'] = SentenceTransformerEmbedder(config['embed_text_model_name'], config)
    # LLM
    logger.info("Initializing LLM")
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
    logger.info("Initializing Screenshotter")
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