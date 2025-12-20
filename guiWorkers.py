import os
from pathlib import Path
import time
import logging
import csv
from datetime import datetime
# 3rd Party
from PIL import Image, ImageGrab
import pillow_heif
from PySide6.QtCore import QThread, Signal, QSize, Qt
from PySide6.QtGui import QImage, QImageReader
# Internal
from searchCoordinator import Prompter

# Register HEIC opener
pillow_heif.register_heif_opener()

logger = logging.getLogger("GUIWorkers")

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv('LOCALAPPDATA')) / "2nd Brain"

# To save a search history:
def record_search_history(query):
    log_file = DATA_DIR / "search_history.csv"
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    try:
        with open(log_file, mode='a', newline='', encoding='utf-8') as f:
            writer = csv.writer(f)
            writer.writerow([timestamp, query])
    except Exception as e:
        logger.error(f"Failed to log search: {e}")

# --- HELPER: THREAD-SAFE IMAGE LOADER ---
def load_qimage_from_path(path):
    """Loads an image from disk safely in a background thread."""
    path_str = str(path)
    ext = Path(path).suffix.lower()
    
    try:
        if ext in ['.heic', '.heif']:
            img = Image.open(path_str)
            img.thumbnail((200, 200))
            if img.mode != "RGBA":
                img = img.convert("RGBA")
            data = img.tobytes("raw", "RGBA")
            return QImage(data, img.width, img.height, QImage.Format_RGBA8888).copy()
        else:
            reader = QImageReader(path_str)
            if reader.canRead():
                orig_size = reader.size()
                target_size = orig_size.scaled(QSize(200, 200), Qt.KeepAspectRatio)
                reader.setScaledSize(target_size)
                return reader.read()
            return None
    except Exception as e:
        logger.error(f"Thumbnail load failed for {path}: {e}")
        return None

# --- WORKER THREADS ---

class SearchWorker(QThread):
    text_ready = Signal(list)
    image_stream = Signal(dict, QImage)
    finished = Signal()

    def __init__(self, engine, searchfacts, filter_folder):
        super().__init__()
        self.engine = engine
        self.searchfacts = searchfacts
        self._is_running = True
        self.filter_folder = filter_folder

    def run(self):
        if not self._is_running: return
        text_res = self.engine.hybrid_search(self.searchfacts.query, "text", top_k=30, folder_path=self.filter_folder)
        self.text_ready.emit(text_res)

        self.searchfacts.text_search_results = text_res
        
        if not self._is_running: return
        image_res = self.engine.hybrid_search(self.searchfacts.query, "image", top_k=30, folder_path=self.filter_folder)

        self.searchfacts.image_search_results = image_res
        
        for item in image_res:
            if not self._is_running: break
            qimg = load_qimage_from_path(item['path'])
            if qimg is None: qimg = QImage()
            self.image_stream.emit(item, qimg)
            
        self.finished.emit()

class LLMWorker(QThread):
    chunk_ready = Signal(str) # Signal to send text back to GUI
    finished = Signal()

    def __init__(self, llm_model, searchfacts, config):
        super().__init__()
        self.temperature = config['temperature']
        self.top_n = config['top_n_llm']  # Number of top results to show LLM
        self.llm = llm_model
        self.searchfacts = searchfacts
        self.config = config
        self._is_running = True

    def run(self):
        # Assemble the final prompt from SearchFacts
        query = f"USER'S QUERY:\n'{self.searchfacts.query}'" if self.searchfacts.query else "USER'S QUERY: The user did not provide a specific prompt; focus on their attachment.\n"

        self.searchfacts.attachment_context_string = Path(self.searchfacts.attachment_path).name if self.searchfacts.attachment_path else ""
        
        attachment_context = f"CONTEXT FROM ATTACHMENT:\n{self.searchfacts.attachment_context_string}\n" if self.searchfacts.attachment_context_string else ""
        
        relevant_chunks = [r['content'][:1000] for r in self.searchfacts.text_search_results[:self.top_n]]
        
        joiner_string = "\n---\n"
        
        formatted_chunks = f"{joiner_string.join(relevant_chunks)}" if relevant_chunks else "No text results found; focus on the images."
        
        self.searchfacts.final_prompt = Prompter(config=self.config).rag_prompt.format(
            query=query,
            attachment_context=attachment_context,
            database_results=formatted_chunks)

        image_paths = [r['path'] for r in self.searchfacts.image_search_results[:self.top_n]]
    
        # Run the LLM with the final prompt
        logger.info("Starting LLM response.")
        if not self.llm or not self.llm.loaded:
            # self.chunk_ready.emit("")
            self.finished.emit()
            return

        try:
            # Iterate over the stream generator from your llmClass
            for chunk in self.llm.stream(
                prompt=self.searchfacts.final_prompt, 
                image_paths=image_paths, 
                temperature=self.temperature
                ):
                if not self._is_running: 
                    break
                # Emit the chunk to the main thread
                self.chunk_ready.emit(chunk)
            logger.info(f"LLM response completed")
        except Exception as e:
            self.chunk_ready.emit(f"\n[System] Error during generation: {e}")
        finally:
            self.finished.emit()

    def stop(self):
        self._is_running = False

class StatsWorker(QThread):
    stats_updated = Signal(dict, int)

    def __init__(self, db):
        super().__init__()
        self.db = db
        self.running = True

    def run(self):
        while self.running:
            try:
                if self.db:
                    stats, total = self.db.get_system_stats()
                    self.stats_updated.emit(stats, total)
            except Exception: pass
            time.sleep(2)

    def stop(self):
        self.running = False
        self.wait()

class ModelToggleWorker(QThread):
    finished = Signal(str, bool)

    def __init__(self, models, key, action):
        super().__init__()
        self.models = models
        self.key = key
        self.action = action 

    def run(self):
        try:
            target = self.models.get(self.key)
            if self.key == 'embed':
                targets = [self.models['text'], self.models['image']]
            else:
                targets = [target]

            success = True
            for model in targets:
                if self.action == "load":
                    if not model.load(): success = False
                else:
                    model.unload()
            self.finished.emit(self.key, success)
        except Exception:
            self.finished.emit(self.key, False)

class DatabaseActionWorker(QThread):
    finished = Signal(str)

    def __init__(self, db, orchestrator, action_type, service_key=None):
        super().__init__()
        self.db = db
        self.orchestrator = orchestrator
        self.action_type = action_type 
        self.service_key = service_key

    def run(self):
        try:
            if self.action_type == 'retry_failed':
                self.db.retry_all_failed()
                pending = self.db.get_pending_tasks()
                count = 0
                for path, task_type in pending:
                    self.orchestrator.submit_task(task_type, path, priority=1, mtime=0)
                    count += 1
                self.finished.emit(f"Retried and re-queued {count} failed tasks.")

            elif self.action_type == 'reset_service':
                self.db.reset_service_data(self.service_key)
                pending = self.db.get_pending_tasks()
                count = 0
                for path, task_type in pending:
                    if task_type == self.service_key:
                        self.orchestrator.submit_task(task_type, path, priority=1, mtime=0)
                        count += 1
                self.finished.emit(f"Reset {self.service_key} and re-queued {count} tasks.")
        except Exception as e:
            self.finished.emit(f"Error: {e}")