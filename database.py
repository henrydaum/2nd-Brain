import sqlite3
import threading
from pathlib import Path
from dataclasses import dataclass
from typing import Optional
import time

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

    def _setup_tables(self):
        with self.lock:
            # WAL mode
            self.conn.execute("PRAGMA journal_mode=WAL;")
            
            # CHANGE: Primary Key is now (path, task_type)
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS tasks (
                    path TEXT,
                    task_type TEXT,
                    status TEXT DEFAULT 'PENDING',
                    file_mtime REAL,
                    result TEXT,
                    updated_at REAL,
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
                    engine_name TEXT,
                    updated_at REAL,
                    FOREIGN KEY(path) REFERENCES tasks(path) ON DELETE CASCADE
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
                    PRIMARY KEY(path, chunk_index),
                    FOREIGN KEY(path) REFERENCES tasks(path) ON DELETE CASCADE
                )
            """)

            # 3. LLM Summaries / Analyses
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS llm_analysis (
                    path TEXT PRIMARY KEY,
                    response TEXT,
                    model_name TEXT,
                    updated_at REAL,
                    FOREIGN KEY(path) REFERENCES tasks(path) ON DELETE CASCADE
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
                    VALUES (new.path, new.path || ' ' || COALESCE(new.text_content, ''), 'embed');
                END;
            """)

            # Trigger 2: TEXT - DELETE
            # If you re-embed a file, this automatically clears the old search entries.
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS t_embed_delete AFTER DELETE ON embeddings
                BEGIN
                    DELETE FROM search_index WHERE path = old.path AND source = 'embed';
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

            # Trigger 5: MASTER CLEANUP (When file is deleted entirely)
            self.conn.execute("""
                CREATE TRIGGER IF NOT EXISTS t_task_delete AFTER DELETE ON tasks
                BEGIN
                    DELETE FROM search_index WHERE path = old.path;
                END;
            """)
            self.conn.commit()

    def add_or_update_task(self, path: str, task_type: str, status: str = "PENDING", mtime: float = 0.0):
        with self.lock:
            # CHANGE: Conflict is now on (path, task_type)
            self.conn.execute("""
                INSERT INTO tasks (path, task_type, status, file_mtime, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(path, task_type) DO UPDATE SET
                    status=excluded.status,
                    file_mtime = CASE WHEN excluded.file_mtime > 0 THEN excluded.file_mtime ELSE tasks.file_mtime END,
                    updated_at=excluded.updated_at
            """, (path, task_type, status, mtime, time.time()))
            self.conn.commit()

    # MISC UTILITY FUNCTIONS
    
    def remove_task(self, path: str):
        # Removes ALL tasks for this file (cleanup)
        with self.lock:
            self.conn.execute("DELETE FROM tasks WHERE path=?", (path,))
            self.conn.commit()

    def mark_completed(self, path: str, task_type: str, result: str):
        # CHANGE: We must specify task_type to know WHICH task finished
        import time
        with self.lock:
            self.conn.execute("""
                UPDATE tasks 
                SET status='DONE', result=?, updated_at=? 
                WHERE path=? AND task_type=?
            """, (result, time.time(), path, task_type))
            self.conn.commit()

    def get_pending_tasks(self):
        """Used on startup to resume unfinished work."""
        with self.lock:
            cur = self.conn.execute("SELECT path, task_type FROM tasks WHERE status='PENDING'")
            return cur.fetchall()

    def get_all_file_states(self):
        """
        Returns a dictionary of {file_path: last_modified_timestamp} 
        for efficient 'diffing' against the file system.
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
                "OCR":   {"PENDING": 0, "DONE": 0, "FAILED": 0, "DB_ROWS": 0},
                "EMBED": {"PENDING": 0, "DONE": 0, "FAILED": 0, "DB_ROWS": 0},
                "EMBED_LLM": {"PENDING": 0, "DONE": 0, "FAILED": 0, "DB_ROWS": 0},
                "LLM":   {"PENDING": 0, "DONE": 0, "FAILED": 0, "DB_ROWS": 0}
            }
            
            for t_type, status, count in rows:
                if t_type in stats and status in stats[t_type]:
                    stats[t_type][status] = count

            # 3. VALIDATION: Count actual data rows to ensure integrity
            
            # OCR: One row per file
            cur = self.conn.execute("SELECT COUNT(*) FROM ocr_results")
            stats["OCR"]["DB_ROWS"] = cur.fetchone()[0]

            # EMBED: Multiple chunks per file, so we count DISTINCT paths
            # This ensures the number matches the "DONE" task count
            cur = self.conn.execute("SELECT COUNT(DISTINCT path) FROM embeddings")
            stats["EMBED"]["DB_ROWS"] = cur.fetchone()[0]

            # LLM: One row per file
            cur = self.conn.execute("SELECT COUNT(*) FROM llm_analysis")
            stats["LLM"]["DB_ROWS"] = cur.fetchone()[0]
            
            # 4. Total Unique Files Tracked
            cur = self.conn.execute("SELECT COUNT(DISTINCT path) FROM tasks")
            total_files = cur.fetchone()[0]
            
            return stats, total_files

    # SPECIFIC SERVICES
    
    def save_ocr_result(self, path, text):
        with self.lock:
            self.conn.execute("INSERT OR REPLACE INTO ocr_results VALUES (?, ?, 'winrt', ?)", 
                             (path, text, time.time()))
            self.conn.commit()

    def save_embeddings(self, path, data):
        # data = [(index, text, embedding_bytes, model_name), ...]
        with self.lock:
            self.conn.executemany("INSERT OR REPLACE INTO embeddings VALUES (?, ?, ?, ?, ?)", 
                                 [(path, *c) for c in data])
            self.conn.commit()

    def save_llm_result(self, path, response, model_name="local"):
        with self.lock:
            # CHANGE: Now accepts and saves chunk_index
            self.conn.execute(
                "INSERT OR REPLACE INTO llm_analysis (path, response, model_name, updated_at) VALUES (?, ?, ?, ?)", 
                (path, response, model_name, time.time())
            )
            self.conn.commit()

    # SEARCH FUNCTION

    def search_lexical(self, query, negative_query, limit=20):
        with self.lock:
            final_match_query = query
            if negative_query:
                clean_neg = negative_query.strip()
                if clean_neg:
                    final_match_query = f"{query} NOT {clean_neg}"

            cur = self.conn.execute("""
                SELECT path, content, bm25(search_index) as rank 
                FROM search_index 
                WHERE search_index MATCH ?
                ORDER BY rank 
                LIMIT ?
            """, (final_match_query, limit))
            return cur.fetchall()

    # GUI SETTINGS

    def retry_all_failed(self):
        """Resets all FAILED tasks to PENDING so the Orchestrator picks them up again."""
        with self.lock:
            self.conn.execute("UPDATE tasks SET status='PENDING' WHERE status != 'DONE'")
            self.conn.commit()

    def reset_service_data(self, service_key):
        """
        Destructive: Deletes all data for a specific service and resets its tasks to PENDING.
        service_key: 'OCR', 'EMBED', or 'LLM'
        """
        table_map = {
            'OCR': 'ocr_results',
            'EMBED': 'embeddings',
            'EMBED_LLM': 'embeddings',
            'LLM': 'llm_analysis'
        }
        
        target_table = table_map.get(service_key)
        if not target_table: return

        with self.lock:
            # 1. Nuke the data
            if target_table == 'embeddings' and service_key == 'EMBED_LLM':
                # Special case: EMBED_LLM only deletes negative index embeddings
                self.conn.execute(f"DELETE FROM {target_table} WHERE chunk_index < 0")
            elif target_table == 'embeddings' and service_key == 'EMBED':
                # Special case: EMBED only deletes non-negative index embeddings
                self.conn.execute(f"DELETE FROM {target_table} WHERE chunk_index >= 0")
            else:
                self.conn.execute(f"DELETE FROM {target_table}")

            # 2. Delete EMBED_LLM *tasks* if LLM is reset; other tasks stay the same because they are independent
            if service_key == 'LLM':
                self.conn.execute("DELETE FROM tasks WHERE task_type='EMBED_LLM'")

            # 3. Reset the tasks so they run again
            self.conn.execute("UPDATE tasks SET status='PENDING' WHERE task_type=?", (service_key,))
            self.conn.commit()