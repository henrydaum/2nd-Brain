import queue
import logging
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
import threading
from threading import BoundedSemaphore
import os
# Internal
from services.ocr import OCRService
from services.embed import EmbedService
from services.llm import LLMService

logger = logging.getLogger("Orchestrator")

@dataclass(order=True)
class Job:
    priority: int
    task_type: str = field(compare=False)
    path: str = field(compare=False)

class Orchestrator:
    def __init__(self, db, models, config):
        self.db = db
        self.config = config
        self.models = models # Store raw models reference for availability checks
        
        # --- 1. INITIALIZE REAL SERVICES ---
        # We pass the DB and the specific Model instance to each worker
        self.ocr_service = OCRService(db, models['ocr'])
        self.embed_service = EmbedService(db, models['text'], models['image'], config)
        self.llm_service = LLMService(db, models['llm'], config)
        
        # The Buffer
        self.queue = queue.PriorityQueue()
        self.executor = ThreadPoolExecutor(max_workers=self.config.get('max_workers', 4), thread_name_prefix="Worker")
        self.pool_semaphore = BoundedSemaphore(value=self.config.get('max_workers', 4))

        self.running = False
        self.monitor_thread = None

        # Batching State
        self.BATCH_SIZE = self.config.get('batch_size', 16)
        self.FLUSH_TIMEOUT = self.config.get('flush_timeout', 5.0)
        self.text_buffer = []
        self.last_text_flush = time.time()
        self.image_buffer = []
        self.last_image_flush = time.time()
        self.delete_buffer = []
        self.last_delete_flush = time.time()

        # Timeout Tracking
        self.TASK_TIMEOUT = self.config.get('task_timeout', 300)  # 5 minutes
        self.active_jobs = {}    # Key: (path, task_type), Value: {'start_time': float, 'job': Job}
        self.active_jobs_lock = threading.Lock()
        self.last_timeout_check = time.time()

    def start(self):
        self.running = True
        logger.info(f"Orchestrator started with {self.config.get('max_workers', 4)} workers.")
        
        # Restore state
        pending = self.db.get_pending_tasks()
        for path, task_type in pending:
            # We submit with priority 1 (High) to catch up
            self.submit_task(task_type, path, priority=1)

        self.monitor_thread = threading.Thread(target=self._dispatch_loop, daemon=True)
        self.monitor_thread.start()

    def stop(self):
        self.running = False
        self.executor.shutdown(wait=False, cancel_futures=True)
        logger.info("Orchestrator stopped.")

    def is_model_available(self, task_type):
        """Checks if the required model is online."""
        if task_type == "OCR":
            return self.models['ocr'].loaded
        elif task_type == "EMBED":
            return self.models['text'].loaded and self.models['image'].loaded
        elif task_type == "EMBED_LLM":
            return self.models['text'].loaded
        elif task_type == "LLM":
            return self.models['llm'].loaded
        elif task_type == "DELETE":
            return True
        return False

    def submit_task(self, task_type, path, priority=2, mtime=0.0):
        # 1. Always save to DB first
        self.db.add_or_update_task(path, task_type, "PENDING", mtime=mtime)
        
        # 2. Only queue if model is ready
        if self.is_model_available(task_type):
            job = Job(priority, task_type, path)
            self.queue.put(job)
            logger.debug(f"Queued: {task_type} for {path}")
        else:
            # Otherwise, it sleeps in the DB
            logger.debug(f"Saved (Pending Model): {task_type} for {path}")

    # Wakie wakie!
    def resume_pending(self, task_type):
        """Called by Tray when a model is toggled ON."""
        # logger.info(f"Signal: Waking up {task_type} tasks...")
        
        pending = self.db.get_pending_tasks() 
        count = 0
        for path, t_type in pending:
            if t_type == task_type:
                # The task is already 'PENDING' in the DB, so we just add it to memory.
                job = Job(2, t_type, path)
                self.queue.put(job)
                count += 1
        
        if count > 0:
            logger.info(f"✓ Resumed {count} sleeping tasks.")

    def _dispatch_loop(self):
        while self.running:
            self.pool_semaphore.acquire()  # Only release one task at a time

            try:
                current_time = time.time()
                
                # Check Timers
                if self.text_buffer and (current_time - self.last_text_flush > self.FLUSH_TIMEOUT):
                    self._flush_buffer_embed("text")
                if self.image_buffer and (current_time - self.last_image_flush > self.FLUSH_TIMEOUT):
                    self._flush_buffer_embed("image")
                if self.delete_buffer and (current_time - self.last_delete_flush > self.FLUSH_TIMEOUT):
                    self._flush_buffer_delete()

                # Check Timeouts (about every 5 seconds)
                if current_time - self.last_timeout_check > 5.0:
                    self._check_timeouts()
                    self.last_timeout_check = current_time

                # Get Job
                try:
                    job = self.queue.get(timeout=0.5) 
                except queue.Empty:
                    self.pool_semaphore.release() # Release if no work found
                    continue

                # Route Job
                ext = Path(job.path).suffix.lower()
                img_exts = self.config.get('image_extensions', [])
                text_exts = self.config.get('text_extensions', [])

                if job.task_type == "EMBED":
                    self.pool_semaphore.release() # Release because we aren't using a thread yet
                    if ext in img_exts:
                        self.image_buffer.append(job)
                        if len(self.image_buffer) >= self.BATCH_SIZE:
                            self._flush_buffer_embed("image")
                    elif ext in text_exts:
                        self.text_buffer.append(job)
                        if len(self.text_buffer) >= self.BATCH_SIZE:
                            self._flush_buffer_embed("text")
                    else:
                        self.db.add_or_update_task(job.path, "EMBED", "FAILED")
                
                elif job.task_type == "DELETE":
                    self.pool_semaphore.release() # Release because we aren't using a thread yet
                    self.delete_buffer.append(job)
                    if len(self.delete_buffer) >= self.BATCH_SIZE:
                        self._flush_buffer_delete()

                else:
                    self.executor.submit(self._execute_job_wrapper, job)

            except Exception as e:
                logger.error(f"Dispatch Error: {e}")
                self.pool_semaphore.release()

    def _check_timeouts(self):
        """Scans active jobs for stuck threads."""
        now = time.time()
        to_remove = []

        with self.active_jobs_lock:
            for key, data in self.active_jobs.items():
                if now - data['start_time'] > self.TASK_TIMEOUT:
                    to_remove.append(data['job'])
            
            for job in to_remove:
                key = (job.path, job.task_type)
                if key in self.active_jobs:
                    del self.active_jobs[key]
                    
                    # Update DB to Failed
                    logger.error(f"✗ TIMEOUT: Task {job.task_type} for {Path(job.path).name} exceeded {self.TASK_TIMEOUT}s. Abandoning thread.")
                    self.db.add_or_update_task(job.path, job.task_type, "FAILED")
                    
                    # Release Semaphore so new work can enter
                    self.pool_semaphore.release()

    def _execute_job_wrapper(self, job):
        # Create a unique key for this specific task
        key = (job.path, job.task_type)
        
        # --- 1. CLOCK IN (Register Start Time) ---
        with self.active_jobs_lock:
            self.active_jobs[key] = {'start_time': time.time(), 'job': job}

        try:
            self._execute_job(job)
        finally:
            # --- 2. CLOCK OUT (Cleanup) ---
            # Only remove it if the timeout checker hasn't already removed it!
            with self.active_jobs_lock:
                if key in self.active_jobs:
                    del self.active_jobs[key]
                    # Release the semaphore normally
                    self.pool_semaphore.release() 
                else:
                    # If we are NOT in the list, it means _check_timeouts removed us.
                    # We do NOT release the semaphore here because _check_timeouts 
                    # already did it to unblock the queue.
                    pass

    def _flush_buffer_embed(self, batch_type):
        target_buffer = self.text_buffer if batch_type == "text" else self.image_buffer
        if not target_buffer: return

        batch_jobs = list(target_buffer)
        if batch_type == "text":
            self.text_buffer = []
            self.last_text_flush = time.time()
        else:
            self.image_buffer = []
            self.last_image_flush = time.time()
            
        logger.info(f"Dispatching {batch_type.upper()} Batch: {len(batch_jobs)} files")
        self.executor.submit(self._execute_batch_embed, batch_jobs, batch_type)
    
    def _flush_buffer_delete(self):
        if not self.delete_buffer: return

        batch_jobs = list(self.delete_buffer)
        self.delete_buffer = []
        self.last_delete_flush = time.time()
        
        logger.info(f"Dispatching DELETE Batch: {len(batch_jobs)} files")
        self.executor.submit(self._execute_batch_delete, batch_jobs)

    # --- 3. DELEGATE TO REAL SERVICES ---

    def _execute_batch_delete(self, jobs):
        try:
            self.db.remove_tasks_bulk([job.path for job in jobs])
            logger.info(f"✓ Deleted: {len(jobs)} tasks from DB.")
        except Exception as e:
            logger.error(f"Batch Delete Error: {e}")
    
    def _execute_batch_embed(self, jobs, batch_type):
        try:
            # Return immediately if model is not loaded, which leaves the job as 'PENDING'
            target_model = self.models['text'] if batch_type == "text" else self.models['image']
            if not target_model.loaded:
                return

            # Run Batch
            success_paths = self.embed_service.run_batch(jobs, batch_type)
            success_set = set(success_paths)
            
            # Mark ONLY 'EMBED' as Done. Do not trigger LLM.
            for job in jobs:
                if job.path in success_set:
                    # SUCCESS: Mark as Done
                    self.db.mark_completed(job.path, "EMBED")
                else:
                    # FAILURE: Mark as FAILED (e.g. model was unloaded)
                    self.db.add_or_update_task(job.path, "EMBED", "FAILED")
                
        except Exception as e:
            logger.error(f"Batch Error: {e}")
            for job in jobs:
                 self.db.add_or_update_task(job.path, "EMBED", "FAILED")

    def _execute_job(self, job: Job):
        try:
            if job.task_type == "OCR":
                # Exit early, task stays pending for next time.
                if not self.models['ocr'].loaded:
                    return
                success = self.ocr_service.run(job)
                if success:
                    self.db.mark_completed(job.path, "OCR")
                else:
                    # FAILURE: Mark as FAILED (e.g. model was unloaded)
                    self.db.add_or_update_task(job.path, "OCR", "FAILED")

            elif job.task_type == "LLM":
                # Exit early, task stays pending for next time.
                if not self.models['llm'].loaded:
                    return
                success = self.llm_service.run(job)
                if success:
                    self.db.mark_completed(job.path, "LLM")
                    # Make the new task for embedding the summary, with high prio
                    try:
                        mtime = os.path.getmtime(job.path)
                        self.submit_task("EMBED_LLM", job.path, priority=1, mtime=mtime)
                        # logger.info("Queued a new task for summary embedding.")
                    except OSError:
                        logger.warning(f"Analysis saved, but could not queue embedding for {job.path} (File missing)")
                else:
                    # FAILURE: Mark as FAILED (e.g. model was unloaded)
                    self.db.add_or_update_task(job.path, "LLM", "FAILED")

            elif job.task_type == "EMBED_LLM":
                # Exit early, task stays pending for next time.
                if not (self.models['text'].loaded):
                    return
                # logger.info(f"Starting summary embedding for: {Path(job.path).name}")
                success = self.embed_service.run_embed_llm(job)
                if success:
                    self.db.mark_completed(job.path, "EMBED_LLM")
                else:
                    # FAILURE: Mark as FAILED (e.g. model was unloaded)
                    self.db.add_or_update_task(job.path, "EMBED_LLM", "FAILED")

        except Exception as e:
            logger.error(f"Task Failed: {e}")
            self.db.add_or_update_task(job.path, job.task_type, "FAILED")
        finally:
            self.queue.task_done()