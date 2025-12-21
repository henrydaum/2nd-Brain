import logging
import re
import os
import numpy as np
from pathlib import Path

logger = logging.getLogger("SearchEngine")

# --- HELPER: TEXT NORMALIZATION ---
def normalize_text(text):
    """
    Simple tokenizer for Jaccard Similarity (Lexical Diversity).
    """
    if not text: return []
    text = re.sub(r'[^a-zA-Z0-9\s]', '', text.lower())
    return text.split()

# --- HELPER: MMR RERANKING ---
def mmr_rerank_hybrid(results, mmr_lambda=0.5, alpha=0.5, n_results=20):
    """
    Maximal Marginal Relevance with Hybrid (Semantic + Lexical) Diversity.
    """
    if not results: return []

    embeddings = np.array([r["embedding"] for r in results])
    relevance_scores = np.array([r["score"] for r in results])
    n = len(results)
    
    # 1. Semantic Similarity Matrix
    semantic_sim_matrix = np.dot(embeddings, embeddings.T)
    semantic_sim_matrix = np.clip(semantic_sim_matrix, 0, 1)

    # 2. Lexical Similarity Matrix
    token_sets = [set(normalize_text(r["content"])) for r in results]
    lexical_sim_matrix = np.zeros((n, n))
    
    for i in range(n):
        for j in range(i, n):
            tokens1 = token_sets[i]
            tokens2 = token_sets[j]
            if not tokens1 and not tokens2:
                sim = 1.0
            else:
                intersect = len(tokens1.intersection(tokens2))
                union = len(tokens1.union(tokens2))
                sim = intersect / union if union != 0 else 0.0
            lexical_sim_matrix[i, j] = sim
            lexical_sim_matrix[j, i] = sim

    # 3. Hybrid Matrix
    hybrid_sim_matrix = (alpha * semantic_sim_matrix) + ((1 - alpha) * lexical_sim_matrix)
    np.fill_diagonal(hybrid_sim_matrix, -1) 

    # 4. MMR Selection
    selected_indices = []
    remaining_indices = list(range(n))

    first_idx = np.argmax(relevance_scores)
    selected_indices.append(first_idx)
    remaining_indices.remove(first_idx)

    while len(selected_indices) < n_results and remaining_indices:
        sims_to_selected = hybrid_sim_matrix[remaining_indices][:, selected_indices]
        max_sim_to_selected = np.max(sims_to_selected, axis=1)
        
        mmr_scores = (mmr_lambda * relevance_scores[remaining_indices]) - \
                     ((1 - mmr_lambda) * max_sim_to_selected)
        
        best_idx_in_remaining = np.argmax(mmr_scores)
        best_idx = remaining_indices[best_idx_in_remaining]
        
        selected_indices.append(best_idx)
        remaining_indices.remove(best_idx)

    return [results[i] for i in selected_indices]


# --- MAIN SEARCH ENGINE ---
class SearchEngine:
    def __init__(self, db, models, config):
        self.db = db
        self.models = models
        self.config = config

    def _build_path_filter(self, folder_path):
        """Creates the SQL clause and params for folder filtering."""
        if not folder_path or folder_path == "All":
            return "", []
        
        clean_path = os.path.normpath(folder_path)
        like_pattern = f"{clean_path}%"
        return " AND path LIKE ?", [like_pattern]

    def _hydrate_ocr(self, results):
        """
        Populates empty content fields for image results using OCR data from the DB.
        """
        # Identify results that need hydration
        candidates = {}
        for r in results:
            # Check for image type and empty/placeholder content
            if r.get('type') == 'image' and (not r.get('content') or r.get('content') == "[IMAGE]"):
                # Use normalized path as key to handle slash mismatches
                norm_path = os.path.normpath(r['path'])
                candidates[norm_path] = r
        
        if not candidates:
            return results

        try:
            # 1. Fetch from 'ocr_results' table
            raw_paths = [r['path'] for r in candidates.values()]
            placeholders = ",".join(["?"] * len(raw_paths))
            
            # FIXED: Correct Table (ocr_results) and Column (text_content)
            sql = f"SELECT path, text_content FROM ocr_results WHERE path IN ({placeholders})"
            
            with self.db.lock:
                cur = self.db.conn.execute(sql, raw_paths)
                rows = cur.fetchall()
            
            # Build lookup map with NORMALIZED keys
            text_map = {}
            for row in rows:
                p = os.path.normpath(row[0])
                text_map[p] = row[1]
                
            # Update results
            for norm_path, r in candidates.items():
                if norm_path in text_map and text_map[norm_path]:
                    r['content'] = text_map[norm_path]
                    
        except Exception as e:
            logger.error(f"OCR Hydration failed: {e}")
            
        return results

    def get_lexical(self, query, search_type, top_k=20, folder_path=None):
        try:
            rows = self.db.search_lexical(query, search_type, limit=top_k * 2) 
            
            best_per_file = {}
            paths_needing_vectors = []
            
            filter_prefix = None
            if folder_path and folder_path != "All":
                filter_prefix = os.path.normpath(folder_path)

            for path, content, ftype, rank in rows:
                if filter_prefix:
                    if not os.path.normpath(path).startswith(filter_prefix):
                        continue

                score = -1 * float(rank)
                
                if path not in best_per_file or score > best_per_file[path]['score']:
                    res = {
                        "path": path,
                        "content": content,
                        "type": ftype,
                        "score": score,
                        "method": "lexical",
                        "match_type": "Lexical",
                        "embedding": None 
                    }
                    best_per_file[path] = res
                    if path not in paths_needing_vectors:
                        paths_needing_vectors.append(path)
                
                if len(best_per_file) >= top_k: break

            results = list(best_per_file.values())
            
            if paths_needing_vectors:
                placeholders = ",".join(["?"] * len(paths_needing_vectors))
                sql = f"SELECT path, embedding FROM embeddings WHERE chunk_index=0 AND path IN ({placeholders})"
                
                with self.db.lock:
                    cur = self.db.conn.execute(sql, paths_needing_vectors)
                    vec_map = {row[0]: row[1] for row in cur.fetchall()}
                
                for res in results:
                    if res['path'] in vec_map:
                        res['embedding'] = np.frombuffer(vec_map[res['path']], dtype=np.float32)
            
            return results

        except Exception as e:
            logger.error(f"Lexical Search Failed: {e}")
            return []

    def get_semantic(self, query, search_type, top_k=20, folder_path=None):
        try:
            model = self.models.get(search_type)
            if search_type == 'image':
                valid_exts = self.config.get('image_extensions', [])
            else:
                valid_exts = self.config.get('text_extensions', [])

            if not model.loaded or not valid_exts: 
                return []

            prefix = "Represent this sentence for searching relevant passages: " if self.config.get('text_model_name', True) in ["BAAI/bge-small-en-v1.5", "BAAI/bge-large-en-v1.5"] else ""
            prefixed_query = prefix + query

            query_embeddings = model.encode([prefixed_query])
            if query_embeddings is None: return []
            
            query_vec = query_embeddings[0]
            norm = np.linalg.norm(query_vec)
            if norm > 0: query_vec = query_vec / norm

            ext_clauses = [f"path LIKE '%{ext}'" for ext in valid_exts]
            ext_query = "(" + " OR ".join(ext_clauses) + ")"
            
            sql_params = []
            
            filter_clause, filter_params = self._build_path_filter(folder_path)
            if filter_clause:
                ext_query += filter_clause
                sql_params.extend(filter_params)

            sql = f"SELECT path, embedding, text_content FROM embeddings WHERE {ext_query}"
            
            with self.db.lock:
                cur = self.db.conn.execute(sql, sql_params)
                rows = cur.fetchall()
            
            if not rows: return []

            best_per_file = {} 

            for path, blob, content in rows:
                vec = np.frombuffer(blob, dtype=np.float32)
                v_norm = np.linalg.norm(vec)
                if v_norm > 0: vec = vec / v_norm

                score = float(np.dot(query_vec, vec))
                
                if path not in best_per_file or score > best_per_file[path]['score']:
                    best_per_file[path] = {
                        "path": path,
                        "content": content,
                        "type": search_type,
                        "score": score,
                        "method": "semantic",
                        "match_type": "Semantic",
                        "embedding": vec 
                    }
            
            results = list(best_per_file.values())
            results.sort(key=lambda x: x['score'], reverse=True)
            
            return results[:top_k]

        except Exception as e:
            logger.error(f"Semantic Search Failed: {e}")
            return []

    def hybrid_search(self, query, search_type, top_k=10, folder_path=None):
        """
        Main entry point.
        """
        fetch_k = top_k * 2
        lex_results = self.get_lexical(query, search_type, top_k=fetch_k, folder_path=folder_path)
        sem_results = self.get_semantic(query, search_type, top_k=fetch_k, folder_path=folder_path)
        
        def normalize_scores(result_list):
            if not result_list: return
            scores = np.array([r['score'] for r in result_list])
            if scores.max() == scores.min():
                norm_scores = np.ones_like(scores)
            else:
                norm_scores = (scores - scores.min()) / (scores.max() - scores.min())
            for i, r in enumerate(result_list):
                r['score'] = float(norm_scores[i])

        normalize_scores(lex_results)
        normalize_scores(sem_results)

        combined_map = {}
        def merge_in(results):
            for r in results:
                key = r['path']
                match_type = r['match_type']
                if key not in combined_map:
                    combined_map[key] = r
                else:
                    existing = combined_map[key]
                    existing['score'] = (existing['score'] + r['score']) / 2.0
                    existing['match_type'] = "Hybrid"

        merge_in(lex_results)
        merge_in(sem_results)
        
        combined_results = list(combined_map.values())

        model = self.models.get(search_type)
        if model and getattr(model, 'loaded', False):
            missing_indices = [i for i, r in enumerate(combined_results) if r['embedding'] is None]
            if missing_indices:
                try:
                    texts = [str(combined_results[i]['content']) for i in missing_indices]
                    if texts:
                        embeddings = model.encode(texts)
                        if embeddings is not None:
                            for idx, vec in zip(missing_indices, embeddings):
                                norm = np.linalg.norm(vec)
                                if norm > 0: vec = vec / norm
                                combined_results[idx]['embedding'] = vec
                except Exception as e:
                    logger.warning(f"On-the-fly embedding failed: {e}")

        can_run_mmr = all(r['embedding'] is not None for r in combined_results)
        
        if can_run_mmr and combined_results:
             reranked_results = mmr_rerank_hybrid(
                combined_results, 
                mmr_lambda=self.config.get('mmr_lambda', 0.7),
                alpha=self.config.get('mmr_alpha', 0.5),
                n_results=self.config.get('num_results', 20)
            )
        else:
             combined_results.sort(key=lambda x: x['score'], reverse=True)
             reranked_results = combined_results[:self.config.get('num_results', 20)]

        quality_weight = self.config.get('quality_weight', 0.3)
        paths = [r['path'] for r in reranked_results]
        if paths:
            placeholders = ",".join(["?"] * len(paths))
            sql = f"SELECT path, content FROM llm_analysis WHERE analysis_type='quality' AND path IN ({placeholders})"
            quality_map = {}
            try:
                with self.db.lock:
                    cur = self.db.conn.execute(sql, paths)
                    for row in cur.fetchall():
                        try: quality_map[row[0]] = float(row[1])
                        except: pass
            except Exception: pass

            for r in reranked_results:
                quality = quality_map.get(r['path'], 0.5)
                # Apply quality weighting
                r['score'] = ((1 - quality_weight) * r['score']) + (quality_weight * quality)

        reranked_results.sort(key=lambda x: x['score'], reverse=True)
        
        # Hydrate OCR
        reranked_results = self._hydrate_ocr(reranked_results)
        
        return reranked_results