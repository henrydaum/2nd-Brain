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
                
                # 1. Remove explicitly blacklisted folders
                ignored = self.orchestrator.config.get("ignored_folders", [])
                dirs[:] = [d for d in dirs if d not in ignored]

                # 2. Remove hidden folders (if enabled)
                if self.orchestrator.config.get("skip_hidden_folders", True):
                    dirs[:] = [d for d in dirs if not d.startswith('.')]

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
            timer = threading.Timer(self.debounce_interval, self._submit_to_orchestrator, [path, task_type])
            self.pending_timers[path] = timer
            timer.start()

    def _submit_to_orchestrator(self, path, task_type):
        with self.lock:
            if path in self.pending_timers: del self.pending_timers[path]
            
            # --- 1. HANDLE DELETIONS (Fail-safe) ---
            if task_type == "DELETE":
                self._recursive_delete(path)
                return

            if not os.path.exists(path): return

            # --- 2. HANDLE FOLDERS (The "Suitcase" Logic) ---
            # If a folder is pasted or moved here, we must walk it to find the files inside.
            if os.path.isdir(path):
                logger.info(f"[Sync] scanning directory: {Path(path).name}")
                for root, dirs, files in os.walk(path):

                    # 1. Remove explicitly blacklisted folders
                    ignored = self.config.get("ignored_folders", [])
                    dirs[:] = [d for d in dirs if d not in ignored]

                    # 2. Remove hidden folders (if enabled)
                    if self.config.get("skip_hidden_folders", True):
                        dirs[:] = [d for d in dirs if not d.startswith('.')]

                    for name in files:
                        file_path = str(Path(os.path.join(root, name)))
                        # Only queue if it's a valid file type (e.g., .txt, .png)
                        if self.service.is_valid_file(file_path):
                            mtime = os.path.getmtime(file_path)
                            self.service._queue_all_tasks(file_path, mtime)
                return

            # --- 3. HANDLE SINGLE FILES ---
            # If it's not a folder, we check if it's a file we care about.
            if self.service.is_valid_file(path):
                try:
                    current_mtime = os.path.getmtime(path)
                    last_mtime = self.service._known_mtimes.get(path)

                    # False alarm check (timestamp hasn't changed enough)
                    if last_mtime and abs(current_mtime - last_mtime) < 0.1:
                        return 
                    
                    self.service._known_mtimes[path] = current_mtime
                    logger.info(f"[Sync] Event stable: {task_type} -> {Path(path).name}")
                    self.service._queue_all_tasks(path, current_mtime)
                except OSError:
                    pass

    def _recursive_delete(self, path):
        """Helper to remove a file OR an entire folder from the DB."""
        db_state = self.orchestrator.db.get_all_file_states()
        deleted_path = str(Path(path))
        
        for db_path in db_state:
            # Matches exact file OR any file starting with this folder path
            if db_path == deleted_path or db_path.startswith(deleted_path + os.sep):
                self.orchestrator.submit_task("DELETE", db_path, priority=1)

    # --- EVENT WRAPPERS ---
    # CRITICAL FIX: Removed the "is_valid_file" check at the door.
    # We let everything through to the debouncer so it can decide if it's a folder or file.

    def on_modified(self, event):
        self._debounce_task(event.src_path, "MODIFIED")

    def on_created(self, event):
        self._debounce_task(event.src_path, "CREATED")

    def on_moved(self, event):
        # Handle both the source (delete) and destination (create)
        self._recursive_delete(event.src_path)
        self._debounce_task(event.dest_path, "MODIFIED")

    def on_deleted(self, event):
        # We bypass debounce for deletes to ensure they happen fast
        self._recursive_delete(event.src_path)