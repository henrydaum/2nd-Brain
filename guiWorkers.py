import os
from pathlib import Path
import time
import logging
import csv
from datetime import datetime
from dataclasses import dataclass, field
# 3rd Party
from PIL import Image, ImageGrab
import pillow_heif
from PySide6.QtCore import QThread, Signal, QSize, Qt
from PySide6.QtGui import QImage, QImageReader

# Register HEIC opener
pillow_heif.register_heif_opener()

logger = logging.getLogger("GUIWorkers")

BASE_DIR = Path(os.path.dirname(os.path.abspath(__file__)))
DATA_DIR = Path(os.getenv('LOCALAPPDATA')) / "2nd Brain"

# Thread-safe image loading
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

# Data class needed to coordinate search information between threads
@dataclass
class SearchFacts:
    """Holds all data for a single user request and its results."""
    from typing import List, Optional, Any, Dict
    query: str = ""
    attachment_path: Optional[Path] = None
    text_attachment: str = None
    image_attachment: str = None
    image_search_results: List[Dict[str, Any]] = field(default_factory=list)
    text_search_results: List[Dict[str, Any]] = field(default_factory=list)

    negative_query: str = ""
    folder_filter: str = ""
    source_filter: dict = field(default_factory=dict)

# --- WORKER THREADS ---

class SearchWorker(QThread):
    """Emits search results back to the GUI thread as they are found, enabling faster UI updates."""
    text_ready = Signal(list)
    image_stream = Signal(dict, QImage)

    def __init__(self, search_engine, searchfacts):
        super().__init__()
        self.search_engine = search_engine
        self.searchfacts = searchfacts
        self._is_running = True

    def run(self):
        """Performs two hybrid seaches: one for text and one for images."""
        if not self._is_running: return

        # Need to process the attachment HERE. For now leave blank.
        if self.searchfacts.attachment_path:
            attachment_ext = Path(self.searchfacts.attachment_path).suffix.lower()
            if attachment_ext in self.search_engine.config['text_extensions']:
                # Extract text here
                ...
            elif attachment_ext in self.search_engine.config['image_extensions']:
                self.searchfacts.image_attachment = self.searchfacts.attachment_path

        # Signify the typle of query for each query, so that the search function knows how to process it.
        query_tuples = []
        if self.searchfacts.query:
            query_tuples.append(("text", self.searchfacts.query))
        if self.searchfacts.text_attachment:
            query_tuples.append(("text", self.searchfacts.text_attachment))
        if self.searchfacts.image_attachment:
            query_tuples.append(("image", self.searchfacts.image_attachment))

        final_results = self.search_engine.hybrid_search(query_tuples, self.searchfacts.negative_query, top_k=30, folder_path=self.searchfacts.folder_filter, valid_sources=self.searchfacts.source_filter)

        text_results = final_results['text']
        image_results = final_results['image']

        self.text_ready.emit(text_results)  # Emit the entire text results at once, because they are small
        
        self.searchfacts.text_search_results = text_results
        self.searchfacts.image_search_results = image_results
        
        # Stream images one by one to avoid UI blocking
        for item in image_results:
            if not self._is_running: break
            qimg = load_qimage_from_path(item['path'])
            if qimg is None: qimg = QImage()
            self.image_stream.emit(item, qimg)

class LLMWorker(QThread):
    """Handles LLM response generation in a separate thread, emitting text chunks as they are produced. 
    Creates the final prompt from SearchFacts."""
    chunk_ready = Signal(str) # Signal to send text back to GUI
    finished = Signal()

    def __init__(self, llm_model, searchfacts, config):
        super().__init__()
        self.llm = llm_model
        self.searchfacts = searchfacts
        self.config = config
        self._is_running = True

    def run(self):
        # If the LLM model is not loaded, exit early
        if not self.llm or not self.llm.loaded:
            # self.chunk_ready.emit("")
            self.finished.emit()
            return

        temperature = self.config.get('temperature', 0.6)
        top_n = self.config.get('top_n_llm', 5)

        # Assemble the final prompt from SearchFacts.
        final_prompt = ""
        final_prompt += f"USER'S SEARCH QUERY: '{self.searchfacts.query}'\n\n" if self.searchfacts.query else "no query\n\n"
        final_prompt += "TEXT SEARCH RESULTS:\n"
        for i, r in enumerate(self.searchfacts.text_search_results[:top_n]):
            final_prompt += f"PATH: {r['path']} | SCORE: {r['score']:.2f} | CONTENT: {r['content']}\n\n"
        final_prompt += "\n"
        final_prompt += "IMAGE SEARCH RESULTS:\n"
        for i, r in enumerate(self.searchfacts.image_search_results[:top_n]):
            final_prompt += f"PATH: {r['path']} | SCORE: {r['score']:.2f} | CONTENT: {r['content']}\n\n"
        final_prompt += "\n"
        final_prompt += f"{self.config.get('system_prompt', '')}\n\n"
        final_prompt += f"When you cite a search result, you MUST use Markdown link format: [filename.ext](full_file_path). Do this exactly.\n\n"
        final_prompt += f"YOUR RESPONSE:\n"

        image_paths = [r['path'] for r in self.searchfacts.image_search_results[:top_n]]
    
        # Run the LLM with the final prompt
        logger.info(f"Starting LLM response; prompt length: {len(final_prompt)} characters")
        try:
            # Iterate over the stream generator from your llmClass
            for chunk in self.llm.stream(
                prompt=final_prompt, 
                image_paths=image_paths, 
                temperature=temperature
                ):
                if not self._is_running: 
                    break
                # Emit the chunk to the main thread
                self.chunk_ready.emit(chunk)
            logger.info(f"LLM response completed; total length: {len(final_prompt)} characters")
        except Exception as e:
            self.chunk_ready.emit(f"\nLLM Worker error during generation: {e}")
        finally:
            self.finished.emit()

    def stop(self):
        self._is_running = False

class StatsWorker(QThread):
    """Continuously polls the database for system stats and emits them to the GUI."""
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
    """The GUI uses this to talk to the database to retry failed tasks or reset a service's data - settings options."""
    finished = Signal(str)

    def __init__(self, db, orchestrator, action_type, service_keys=None):
        super().__init__()
        self.db = db
        self.orchestrator = orchestrator
        self.action_type = action_type 
        self.service_keys = service_keys

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
                count = 0
                for service_key in self.service_keys:
                    self.db.reset_service_data(service_key)
                    pending = self.db.get_pending_tasks()
                    for path, task_type in pending:
                        if task_type == service_key:
                            self.orchestrator.submit_task(task_type, path, priority=1, mtime=0)
                            count += 1
                self.finished.emit(f"Reset {' '.join(self.service_keys)} and re-queued {count} tasks.")
        except Exception as e:
            self.finished.emit(f"Error: {e}")