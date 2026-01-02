import sqlite3
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import time
import logging

logger = logging.getLogger("Database")

@dataclass
class FileRecord:
    path: str
    status: str
    last_modified: float

class Database:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        # Allow multiple threads to use this connection (with locking)
        self.conn = sqlite3.connect(str(db_path), check_same_thread=False)
        self.lock = threading.Lock() # Application-level lock for safety
        
        self._setup_tables()
        # Start integrity validation in a separate thread
        threading.Thread(target=self.validate_integrity, daemon=True).start()

    def _setup_tables(self):
        with self.lock:
            # WAL (write-ahead logging) mode - read and write can occur simultaneously
            self.conn.execute("PRAGMA journal_mode=WAL;")
            self.conn.execute("PRAGMA cache_size = -50000;")
            
            # Primary Key is (path, task_type)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    path TEXT,
                    task_type TEXT,
                    status TEXT DEFAULT 'PENDING',
                    file_mtime REAL,
                    PRIMARY KEY(path, task_type)
                )
            """)

            # This creates a "Shortcut" that get_system_stats will automatically use.
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_tasks_stats 
                ON tasks (task_type, status);
            """)
            self.conn.commit()

            # SPECIFIC SERVICES

            # 1. OCR Results
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS ocr_results (
                    path TEXT PRIMARY KEY,
                    text_content TEXT,
                    model_name TEXT
                )
            """)

            # 2. Embeddings
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS embeddings (
                    path TEXT,
                    chunk_index INTEGER,
                    text_content TEXT,
                    embedding BLOB,
                    model_name TEXT,
                    PRIMARY KEY(path, chunk_index)
                )
            """)

            # 3. LLM Summaries / Analyses
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_analysis (
                    path TEXT PRIMARY KEY,
                    response TEXT,
                    model_name TEXT
                )
            """)

            # SEARCH INDEX
            self.conn.execute("""
                CREATE VIRTUAL TABLE IF NOT EXISTS search_index 
                USING fts5(path UNINDEXED, content, source UNINDEXED);
            """)

            # --- SEARCH INDEX TRIGGERS FOR AUTOMATIC SYNC ---
            # Trigger 1: TEXT - INSERT
            # Concatenates Path + Space + Content.
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS t_embed_insert AFTER INSERT ON embeddings
                BEGIN
                    INSERT INTO search_index (path, content, source) 
                    VALUES (
                        new.path, 
                        new.path || ' ' || COALESCE(new.text_content, ''), 
                        CASE WHEN new.chunk_index < 0 THEN 'llm' ELSE 'embed' END
                    );
                END;
            """)

            # Trigger 2: TEXT - DELETE
            # If you re-embed a file, this automatically clears the old search entries.
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS t_embed_delete AFTER DELETE ON embeddings
                BEGIN
                    DELETE FROM search_index 
                    WHERE path = old.path 
                    AND source = CASE WHEN old.chunk_index < 0 THEN 'llm' ELSE 'embed' END;
                END;
            """)

            # Trigger 3: IMAGE (OCR) - INSERT
            # Concatenates Path + Space + OCR Text.
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS t_ocr_insert AFTER INSERT ON ocr_results
                BEGIN
                    INSERT INTO search_index (path, content, source) 
                    VALUES (new.path, new.path || ' ' || COALESCE(new.text_content, ''), 'ocr');
                END;
            """)

            # Trigger 4: IMAGE (OCR) - DELETE
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS t_ocr_delete AFTER DELETE ON ocr_results
                BEGIN
                    DELETE FROM search_index WHERE path = old.path AND source = 'ocr';
                END;
            """)

            # LLM Analysis triggers not needed because they are embedded and then inserted via the embeddings trigger.
            self.conn.commit()

    # STATE MANAGEMENT

    def add_or_update_task(self, path: str, task_type: str, status: str = "PENDING", mtime: float = 0.0):
        with self.lock:
            # Key is (path, task_type)
            self.conn.execute("""
                INSERT INTO tasks (path, task_type, status, file_mtime)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(path, task_type) DO UPDATE SET
                    status=excluded.status,
                    file_mtime = CASE WHEN excluded.file_mtime > 0 THEN excluded.file_mtime ELSE tasks.file_mtime END
            """, (path, task_type, status, mtime))
            self.conn.commit()

    def remove_tasks_bulk(self, paths: list[str]):
        """Delete all traces for many paths in as few transactions as possible."""
        with self.lock:
            try:
                if not paths:
                    return
                # Temporarily disable triggers to avoid overhead
                self.conn.execute("DROP TRIGGER IF EXISTS t_embed_delete;")
                self.conn.execute("DROP TRIGGER IF EXISTS t_ocr_delete;")
                # Delete all the data
                placeholders = ",".join("?" * len(paths))
                self.conn.execute(f"DELETE FROM ocr_results    WHERE path IN ({placeholders})", paths)
                self.conn.execute(f"DELETE FROM embeddings     WHERE path IN ({placeholders})", paths)
                self.conn.execute(f"DELETE FROM llm_analysis   WHERE path IN ({placeholders})", paths)
                self.conn.execute(f"DELETE FROM search_index   WHERE path IN ({placeholders})", paths)
                self.conn.execute(f"DELETE FROM tasks          WHERE path IN ({placeholders})", paths)
                self.conn.commit()
                return True
            except Exception as e:
                logger.error(f"Bulk delete failed: {e}")
                return False
            finally:
                restore_triggers_sql = """
                    CREATE TRIGGER IF NOT EXISTS t_embed_delete AFTER DELETE ON embeddings
                    BEGIN
                        DELETE FROM search_index 
                        WHERE path = old.path 
                        AND source = CASE WHEN old.chunk_index < 0 THEN 'llm' ELSE 'embed' END;
                    END;

                    CREATE TRIGGER IF NOT EXISTS t_ocr_delete AFTER DELETE ON ocr_results
                    BEGIN
                        DELETE FROM search_index WHERE path = old.path AND source = 'ocr';
                    END;
                """
                self.conn.executescript(restore_triggers_sql)
                self.conn.commit()

    def mark_completed(self, path: str, task_type: str):
        # CHANGE: We must specify task_type to know WHICH task finished
        with self.lock:
            self.conn.execute("""
                UPDATE tasks 
                SET status='DONE'
                WHERE path=? AND task_type=?
            """, (path, task_type))
            self.conn.commit()

    # DATA RETRIEVAL FUNCTIONS

    def get_pending_tasks(self):
        """Used on startup to resume unfinished work."""
        with self.lock:
            cur = self.conn.execute("SELECT path, task_type FROM tasks WHERE status='PENDING'")
            return cur.fetchall()

    def get_all_file_states(self):
        """
        Returns a dictionary of {file_path: last_modified_timestamp} for efficient 'diffing' against the file system.
        """
        with self.lock:
            cur = self.conn.execute("SELECT path, file_mtime FROM tasks")
            return {row[0]: row[1] for row in cur.fetchall()}

    def get_llm_result(self, path: str) -> Optional[str]:
        with self.lock:
            cur = self.conn.execute("SELECT response FROM llm_analysis WHERE path=?", (path,))
            row = cur.fetchone()
            return row[0] if row else None

    def get_system_stats(self):
        """
        Returns a raw snapshot of the system's brain, including:
        1. Task Queue Status (PENDING/DONE/FAILED)
        2. Actual stored data counts (Validation)
        """
        with self.lock:
            # 1. Group by Task and Status (Efficient Single Query)
            cur = self.conn.execute("""
                SELECT task_type, status, COUNT(*) 
                FROM tasks 
                GROUP BY task_type, status
            """)
            rows = cur.fetchall()

            # 2. Organize into a clean dictionary
            stats = {
                "OCR":   {"PENDING": 0, "DONE": 0, "FAILED": 0},
                "EMBED": {"PENDING": 0, "DONE": 0, "FAILED": 0},
                "EMBED_LLM": {"PENDING": 0, "DONE": 0, "FAILED": 0},
                "LLM":   {"PENDING": 0, "DONE": 0, "FAILED": 0}
            }
            
            for t_type, status, count in rows:
                if t_type in stats and status in stats[t_type]:
                    stats[t_type][status] = count
            
            # 4. Total Unique Files Tracked
            cur = self.conn.execute("SELECT COUNT(DISTINCT path) FROM tasks")
            total_files = cur.fetchone()[0]
            
            return stats, total_files

    # SAVE FUNCTIONS FOR SERVICES
    
    def save_ocr_result(self, path, text, model_name):
        with self.lock:
            self.conn.execute("""
                INSERT OR REPLACE INTO ocr_results (path, text_content, model_name) 
                VALUES (?, ?, ?)
            """, (path, text, model_name))
            self.conn.commit()

    def save_embeddings(self, data):
        with self.lock:
            # 1. Identify paths and the TYPE of update (Content vs. Summary)
            paths = set(row[0] for row in data)
            indices = [row[1] for row in data]
            # If saving LLM responses (negative index), only wipe old LLM Embeddings.
            # If saving Content (positive index), only wipe old Chunks.
            is_llm_update = any(idx < 0 for idx in indices)
            if paths:
                placeholders = ','.join(['?'] * len(paths))
                if is_llm_update:
                    # Only delete old LLM summaries (chunk_index < 0)
                    self.conn.execute(
                        f"DELETE FROM embeddings WHERE chunk_index < 0 AND path IN ({placeholders})", 
                        list(paths)
                    )
                else:
                    # Only delete old Content chunks (chunk_index >= 0)
                    self.conn.execute(
                        f"DELETE FROM embeddings WHERE chunk_index >= 0 AND path IN ({placeholders})", 
                        list(paths)
                    )
            # 2. Insert the new data
            self.conn.executemany("""
                INSERT INTO embeddings (path, chunk_index, text_content, embedding, model_name)
                VALUES (?, ?, ?, ?, ?)
            """, data)
            self.conn.commit()

    def save_llm_result(self, path, response, model_name):
        with self.lock:
            # CHANGE: Now accepts and saves chunk_index
            self.conn.execute("""
                INSERT OR REPLACE INTO llm_analysis (path, response, model_name) 
                VALUES (?, ?, ?)
            """, (path, response, model_name))
            self.conn.commit()

    # SEARCH FUNCTION

    def search_lexical(self, query, limit=20):
        """Performs a lexical search using FTS5 index, looking for results which have ALL the words in the query. For example, 'good cow' needs a document with both 'good' AND 'cow'."""
        with self.lock:
            cur = self.conn.execute("""
                SELECT path, content, source, bm25(search_index) as rank 
                FROM search_index 
                WHERE search_index MATCH ?
                ORDER BY rank 
                LIMIT ?
            """, (query, limit))
            return cur.fetchall()

    # GUI SETTINGS

    def retry_all_failed(self):
        """Resets all FAILED tasks to PENDING so the Orchestrator picks them up again."""
        with self.lock:
            self.conn.execute("UPDATE tasks SET status='PENDING' WHERE status != 'DONE'")
            self.conn.commit()
        logger.info("Reset all FAILED tasks to PENDING.")

    def reset_service_data(self, service_key):
        """
        Destructive: Deletes all data for a specific service and resets its tasks to PENDING.
        service_key: 'OCR', 'EMBED', or 'LLM'
        Disables and enables triggers to avoid overhead during bulk deletions.
        """
        with self.lock:
            try:
                # Temporarily disable triggers to avoid overhead
                self.conn.execute("DROP TRIGGER IF EXISTS t_embed_delete;")
                self.conn.execute("DROP TRIGGER IF EXISTS t_ocr_delete;")
                self.conn.commit()

                if service_key == 'OCR':
                    self.conn.execute("DELETE FROM search_index WHERE source = 'ocr'")
                    self.conn.execute("DELETE FROM ocr_results")
                    self.conn.execute("UPDATE tasks SET status='PENDING' WHERE task_type='OCR'")

                elif service_key == 'EMBED':
                    self.conn.execute("DELETE FROM search_index WHERE source = 'embed'")
                    self.conn.execute("DELETE FROM search_index WHERE source = 'llm'")
                    self.conn.execute("DELETE FROM embeddings")
                    self.conn.execute("UPDATE tasks SET status='PENDING' WHERE task_type='EMBED'")
                    self.conn.execute("UPDATE tasks SET status='PENDING' WHERE task_type='EMBED_LLM'")

                elif service_key == 'LLM':
                    self.conn.execute("DELETE FROM llm_analysis")
                    self.conn.execute("UPDATE tasks SET status='PENDING' WHERE task_type='LLM'")
                    self.conn.execute("DELETE FROM tasks WHERE task_type='EMBED_LLM'")  # These will be remade
                    # Must delete downstream data
                    self.conn.execute("DELETE FROM search_index WHERE source = 'llm'")
                    self.conn.execute("DELETE FROM embeddings WHERE chunk_index < 0")

                self.conn.commit()
                logger.info(f"Reset all data and tasks for service: {service_key}")
            except Exception as e:
                logger.error(f"Failed to reset data for service {service_key}: {e}")
            finally:
                try:
                    self.conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS t_embed_delete AFTER DELETE ON embeddings
                        BEGIN
                            DELETE FROM search_index 
                            WHERE path = old.path 
                            AND source = CASE WHEN old.chunk_index < 0 THEN 'llm' ELSE 'embed' END;
                        END
                    """)
                    self.conn.execute("""
                        CREATE TRIGGER IF NOT EXISTS t_ocr_delete AFTER DELETE ON ocr_results
                        BEGIN
                            DELETE FROM search_index WHERE path = old.path AND source = 'ocr';
                        END
                    """)
                    self.conn.commit()
                except Exception as e:
                    logger.error(f"Failed to restore triggers: {e}")
    
    # Database healing and maintenance
    def validate_integrity(self):
        """
        Runs physical and logical consistency checks. 
        Auto-heals 'Orphans' (data with no task) and 'Zombies' (DONE tasks with no data).
        """
        with self.lock:
            logger.info("Performing database integrity check...")

            # 1. PHYSICAL CHECK (Corruption)
            try:
                # Reindex
                self.conn.execute("REINDEX;")
                # This checks for disk-level corruption (broken pages, bad indices)
                cursor = self.conn.execute("PRAGMA integrity_check;")
                result = cursor.fetchone()[0]
                if result != "ok":
                    logger.error(f"CRITICAL: Database corruption detected: {result}")
                    # In a production app, you might backup and recreate the DB here.
            except Exception as e:
                logger.error(f"Integrity check failed: {e}")

            # 2. LOGICAL CHECK (Orphans - Delete data that has no Task)
            # OCR Orphans
            self.conn.execute("""
                DELETE FROM ocr_results 
                WHERE path NOT IN (SELECT path FROM tasks WHERE task_type='OCR')
            """)
            
            # Embedding Orphans (Text)
            self.conn.execute("""
                DELETE FROM embeddings 
                WHERE chunk_index >= 0 
                AND path NOT IN (SELECT path FROM tasks WHERE task_type='EMBED')
            """)

            # LLM Orphans (Analysis)
            self.conn.execute("""
                DELETE FROM llm_analysis 
                WHERE path NOT IN (SELECT path FROM tasks WHERE task_type='LLM')
            """)
            
            # Embedding Orphans (Summary/LLM)
            self.conn.execute("""
                DELETE FROM embeddings 
                WHERE chunk_index < 0 
                AND path NOT IN (SELECT path FROM tasks WHERE task_type='EMBED_LLM')
            """)

            # 3. LOGICAL CHECK (Zombies - Reset tasks that claim to be DONE but have no data)
            
            # OCR Zombies
            self.conn.execute("""
                UPDATE tasks SET status='PENDING' 
                WHERE task_type='OCR' AND status='DONE' 
                AND path NOT IN (SELECT path FROM ocr_results)
            """)

            # Embed Zombies
            self.conn.execute("""
                UPDATE tasks SET status='PENDING' 
                WHERE task_type='EMBED' AND status='DONE' 
                AND path NOT IN (SELECT path FROM embeddings WHERE chunk_index >= 0)
            """)
            
            # LLM Zombies
            self.conn.execute("""
                UPDATE tasks SET status='PENDING' 
                WHERE task_type='LLM' AND status='DONE' 
                AND path NOT IN (SELECT path FROM llm_analysis)
            """)

            # Embed LLM Zombies
            self.conn.execute("""
                UPDATE tasks SET status='PENDING' 
                WHERE task_type='EMBED_LLM' AND status='DONE' 
                AND path NOT IN (SELECT path FROM embeddings WHERE chunk_index < 0)
            """)

            self.conn.commit()

            try:
                # VACUUM to optimize the database after deletions
                self.conn.execute("VACUUM;")
                # Optimize WAL
                self.conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
            except Exception as e:
                logger.error(f"Database optimization failed: {e}")            

            logger.info("Database integrity validation and optimization complete.")