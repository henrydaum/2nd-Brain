import os
from pathlib import Path
import time
import time
import logging
import threading
# 3rd Party
from watchdog.observers import Observer
from watchdog.events import FileSystemEventHandler

logger = logging.getLogger("Watcher")

class FileWatcherService:
    def __init__(self, orchestrator, config):
        self.orchestrator = orchestrator
        self.config = config
        self.observer = Observer()
        # Robustly handle list OR string input
        raw_sync = config.get("sync_directories")
        if isinstance(raw_sync, list):
            self.watch_dirs = raw_sync
        else:
            logger.error("In config.json, sync_directories must be a list.")
        # This is to get around an issue where viewing an image triggers a modify event (held in memory):
        self._known_mtimes = {}
        # These are all the valid extensions:
        self.image_extensions = config.get('image_extensions', [])
        # logger.info(f"Tracking image extensions: {self.image_extensions}")
        self.text_extensions = config.get('text_extensions', [])
        # logger.info(f"Tracking text extensions: {self.text_extensions}")

    def start(self):
        # Validate directories before starting
        valid_dirs = []
        for d in self.watch_dirs:
            if d and os.path.exists(d):
                valid_dirs.append(d)
            else:
                logger.error(f"Sync directory not found: {d}")

        if not valid_dirs:
            logger.error("No valid sync directories found. Watcher aborting.")
            return

        logger.info("Performing initial sync scan...")
        # Pass the list of valid dirs to the scanner
        self._run_initial_scan(valid_dirs)

        # Schedule the SAME event handler for MULTIPLE directories
        event_handler = DebouncedEventHandler(self.orchestrator, self.config, self)
        
        for d in valid_dirs:
            self.observer.schedule(event_handler, d, recursive=True)
            logger.info(f"Watcher Service monitoring: {d}")
            
        self.observer.start()

    def stop(self):
        if self.observer.is_alive():
            self.observer.stop()
            self.observer.join()
        logger.info("Watcher Service stopped.")

    def is_valid_file(self, path):
        p = Path(path)
        if p.is_dir(): return False
        if p.name.startswith('.'): return False
        if p.name.startswith('~$'): return False
        # If the path of a task in the database is not in the list of valid paths, remove it. This enables adding or removing extensions without redoing the entire database.
        ext = p.suffix.lower()
        return (ext in self.text_extensions) or (ext in self.image_extensions)

    def _queue_all_tasks(self, path, mtime):
        """Helper to queue ALL independent tasks for a file."""
        ext = Path(path).suffix.lower()
        
        # 1. Embed (text and image)
        if (ext in self.text_extensions) or (ext in self.image_extensions):
            self.orchestrator.submit_task("EMBED", path, priority=2, mtime=mtime)
        
        # 2. LLM (text and image)
        if (ext in self.text_extensions) or (ext in self.image_extensions):
            self.orchestrator.submit_task("LLM", path, priority=2, mtime=mtime)
        
        # 3. OCR (image only)
        if ext in self.image_extensions:
            self.orchestrator.submit_task("OCR", path, priority=2, mtime=mtime)

    def _run_initial_scan(self, valid_dirs):
        """Shotgun approach: If file is modified, queue ALL tasks."""
        db_state = self.orchestrator.db.get_all_file_states()  # From the SQL database
        disk_files = set()
        
        for watch_dir in valid_dirs:
            for root, dirs, files in os.walk(watch_dir):
                for name in files:
                    path = str(Path(os.path.join(root, name)))
                    
                    # 1. This now returns False for unwanted extensions
                    if not self.is_valid_file(path): continue
                    # 2. So they never get added to this set
                    disk_files.add(path)
                    mtime = os.path.getmtime(path)
                    self._known_mtimes[path] = mtime

                    if path not in db_state:
                        logger.info(f"[Sync] Found New: {name}")
                        self._queue_all_tasks(path, mtime)

                    elif abs(mtime - db_state[path]) > 1.0:
                        logger.info(f"[Sync] Found Modified: {name}")
                        self._queue_all_tasks(path, mtime)
        # Cleanup Ghosts
        for db_path in db_state:
            # 3. If you removed .txt from config, .txt files won't be in disk_files
            # So this standard check will catch them and DELETE them.
            if db_path not in disk_files:
                logger.info(f"[Sync] Deleting Ghost: {Path(db_path).name}")
                self.orchestrator.submit_task("DELETE", db_path, priority=0)  # Highest priority
                continue

class DebouncedEventHandler(FileSystemEventHandler):
    def __init__(self, orchestrator, config, parent_service):
        self.orchestrator = orchestrator
        self.config = config
        self.service = parent_service
        self.debounce_interval = 1.0
        self.pending_timers = {}
        self.lock = threading.Lock()

    def _debounce_task(self, path, task_type):
        with self.lock:
            if path in self.pending_timers:
                self.pending_timers[path].cancel()
            # Triggers just one call for multiple rapid events in a time interval
            timer = threading.Timer(self.debounce_interval, self._submit_to_orchestrator, [path, task_type])
            self.pending_timers[path] = timer
            timer.start()

    def _submit_to_orchestrator(self, path, task_type):
        with self.lock:
            if path in self.pending_timers: del self.pending_timers[path]
            if not os.path.exists(path) and task_type != "DELETE": return

            # This is to get around an issue where viewing an image triggers a modify event:
            if task_type != "DELETE":
                try:
                    current_mtime = os.path.getmtime(path)
                    last_mtime = self.service._known_mtimes.get(path)

                    # If timestamp hasn't changed (threshold 0.1s), it's a false alarm (just a read event)
                    if last_mtime and abs(current_mtime - last_mtime) < 0.1:
                        # This happens too often to say every time
                        # logger.info(f"[Watcher] Event false alarm: {task_type} -> {Path(path).name}")
                        return 
                    # Update cache
                    self.service._known_mtimes[path] = current_mtime
                except OSError:
                    return

            logger.info(f"[Sync] Event stable: {task_type} -> {Path(path).name}")
            
            if task_type == "DELETE":
                self.orchestrator.submit_task("DELETE", path, priority=1)
            else:
                try:
                    mtime = os.path.getmtime(path)
                    # Use the same helper as initial scan!
                    self.service._queue_all_tasks(path, mtime)
                except OSError:
                    pass 

    # Event wrappers (Same as before)
    def on_modified(self, event):
        if not self.service.is_valid_file(event.src_path): return
        self._debounce_task(event.src_path, "MODIFIED")
    def on_created(self, event):
        if not self.service.is_valid_file(event.src_path): return
        self._debounce_task(event.src_path, "CREATED")
    def on_moved(self, event):
        if not self.service.is_valid_file(event.dest_path): return
        self._debounce_task(event.dest_path, "MODIFIED")
    def on_deleted(self, event):
        self.orchestrator.submit_task("DELETE", event.src_path, priority=1)