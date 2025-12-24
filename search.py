import logging
import os
import numpy as np
from typing import List, Dict, Optional, Tuple

logger = logging.getLogger("SearchEngine")

class SearchEngine:
    def __init__(self, db, models, config):
        self.db = db
        self.models = models
        self.config = config

    # --- 'DUMB' DATA FETCHERS ---

    def get_lexical(self, query: str, limit: int = 50) -> List[Dict]:
        """
        Raw keyword search. Returns a list of dicts.
        No filtering, no deduplication.
        """
        if not query or not query.strip():
            logger.info("get_lexical no query")
            return []

        try:
            # - Using the updated unpack (path, content, rank)
            rows = self.db.search_lexical(query, None, limit=limit)
            
            results = []
            for path, content, rank in rows:
                results.append({
                    "path": path,
                    "content": content,
                    "score": -1 * float(rank), # Invert rank so higher is better
                    "match_type": "Lexical",
                    "embedding": None,
                    "num_hits": 1
                })
            return results

        except Exception as e:
            logger.error(f"Lexical Fetch Failed: {e}")
            return []

    def get_semantic(self, query_vec: np.ndarray, limit: int = 50, model_name_used: str = None) -> List[Dict]:
        """
        Optimized vector search. 
        Assumes DB vectors are ALREADY normalized (which they are).
        """
        # 1. Quick Guard clauses
        if query_vec is None or len(query_vec) == 0:
            return []

        try:
            # 2. Fetch Data (Optimized Select)
            # Only select what we need. 
            sql = "SELECT path, text_content, embedding, model_name FROM embeddings"
            
            with self.db.lock:
                cur = self.db.conn.execute(sql)
                rows = cur.fetchall()

            if not rows: return []

            paths = []
            contents_list = []
            valid_embeddings = []

            # 3. Filter & Unpack
            for path, text_content, embedding, model_name in rows:
                if embedding:
                    # Convert bytes to numpy 
                    vec = np.frombuffer(embedding, dtype=np.float32)
                    
                    # Critical Check; vector math can't be done on mismatched embedding sizes
                    if model_name == model_name_used:
                        paths.append(path)
                        contents_list.append(text_content)
                        valid_embeddings.append(vec)
            if not valid_embeddings:
                return []

            # 4. Create the 2D Matrix
            # We use vstack to turn a [list of 1D arrays] into a [2D Matrix]
            # Shape becomes (Num_Docs, Embedding_Dim)
            emb_matrix = np.vstack(valid_embeddings)

            # 5. Calculate Scores (Dot Product)
            # Since vectors are pre-normalized, Dot Product == Cosine Similarity
            scores = np.dot(emb_matrix, query_vec)

            # 6. Sort and Pack
            # Get indices of the top scores
            # If fewer results than 'limit', take them all
            k = min(limit, len(scores))
            top_indices = np.argsort(scores)[-k:][::-1]

            results = []
            for idx in top_indices:
                results.append({
                    "path": paths[idx],
                    "content": contents_list[idx],
                    "score": float(scores[idx]),
                    "match_type": "Semantic",
                    "embedding": None, # Save RAM, don't pass this back unless needed
                    "num_hits": 1
                })
            return results

        except Exception as e:
            logger.error(f"Semantic Fetch Failed: {e}")
            return []

    # --- MAIN CONTROLLER ---

    def hybrid_search(self, query_tuples, negative_query: str = "", top_k: int = 10, folder_path: str = None):
        """
        The Brain. Handles embedding, filtering, deduplication, and fusion.
        """
        logger.info(f"Starting hybrid search")

        text_results = []
        image_results = []

        # Add stuff to the lists by searching for each query/attachment
        for query_type, query in query_tuples:
            
            # 1. PREPARE RESOURCES
            if query_type == "text":
                text_vec = self._embed_query(query, negative_query, self.models['text']) if self.models['text'].loaded else []
                image_vec = self._embed_query(query, negative_query, self.models['image']) if self.models['image'].loaded else []
            elif query_type == "image":
                text_vec = []
                image_vec = []
                if self.models['image'].loaded:
                    try:
                        from PIL import Image
                        Image.MAX_IMAGE_PIXELS = None
                        with Image.open(query).convert("RGB") as img:
                            # Use CLIP to embed image directly
                            image_vec = self.models['image'].encode(img)
                    except Exception as e:
                        logger.error(f"Failed to embed image {query}: {e}")

            # 2. FETCH DATA (DUMB)
            # Fetch 10x top_k to allow for collapsing chunks into docs
            fetch_limit = max(200, top_k * 10)
            
            lex_raw = self.get_lexical(query, limit=fetch_limit) if query_type == "text" else []
            text_sem_raw = self.get_semantic(text_vec, limit=fetch_limit, model_name_used=self.models['text'].model_name) if self.models['text'].loaded else []
            image_sem_raw = self.get_semantic(image_vec, limit=fetch_limit, model_name_used=self.models['image'].model_name) if self.models['image'].loaded else []

            logger.info(f"Fetched {len(lex_raw)} lexical, {len(text_sem_raw)} text semantic, {len(image_sem_raw)} image semantic.")
            
            streams = [lex_raw, text_sem_raw, image_sem_raw]

            # 3. FILTER & DEDUPLICATE (Stream by Stream)
            # -----------------------
            def process_stream(raw_results):
                processed_text = {}
                processed_image = {}
                
                for res in raw_results:
                    path = res['path']
                    
                    # Folder Filter
                    if folder_path and folder_path != "All":
                        if not os.path.normpath(path).startswith(os.path.normpath(folder_path)):
                            continue

                    # Determine type
                    is_text = any(path.lower().endswith(ext.lower()) for ext in self.config['text_extensions'])
                    is_image = any(path.lower().endswith(ext.lower()) for ext in self.config['image_extensions'])
                    
                    target_dict = None
                    if is_text: target_dict = processed_text
                    elif is_image: target_dict = processed_image
                    
                    if target_dict is not None:
                        if path not in target_dict:
                            # First hit for this doc in this stream
                            res['num_hits'] = 1 
                            target_dict[path] = res
                        else:
                            # We found another chunk for this doc!
                            # 1. Increment hits on the object currently stored
                            target_dict[path]['num_hits'] += 1
                            
                            # 2. If this new chunk has a better score, swap the content
                            # but preserve the accumulated hit count we just updated.
                            if res['score'] > target_dict[path]['score']:
                                accumulated_hits = target_dict[path]['num_hits']
                                target_dict[path] = res
                                target_dict[path]['num_hits'] = accumulated_hits
                
                return list(processed_text.values()), list(processed_image.values())

            for stream in streams:
                if not stream: continue
                p_text, p_image = process_stream(stream)
                text_results.append(p_text)
                image_results.append(p_image)

        # 4. RECIPROCAL RANK FUSION (RRF)
        # -------------------------------
        
        merged_scores = {'text': {}, 'image': {}}
        merged_docs = {'text': {}, 'image': {}}
        rrf_constant = 60

        def apply_rrf_rank(results_list, media_type):
            # Sort by score descending to establish rank for this specific stream
            results_list.sort(key=lambda x: x['score'], reverse=True)
            
            for rank, item in enumerate(results_list):
                path = item['path']
                
                if path not in merged_docs[media_type]:
                    # New doc for the final list
                    merged_docs[media_type][path] = item
                else:
                    # Doc already found in a previous stream (e.g. was in Lexical, now in Semantic)
                    stored_doc = merged_docs[media_type][path]
                    
                    # A. Mark as Hybrid
                    if stored_doc['match_type'] != item['match_type']:
                        stored_doc['match_type'] = "Hybrid"
                    
                    # B. MERGE HITS (This is what you were missing)
                    # We add the hits from this stream to the existing total
                    stored_doc['num_hits'] += item['num_hits']
                    
                    # C. Keep the highest score/content between streams (Optional but good)
                    if item['score'] > stored_doc['score']:
                         # Update display content to the better chunk, but keep the merged counts
                         current_hits = stored_doc['num_hits']
                         current_match_type = stored_doc['match_type']
                         merged_docs[media_type][path] = item
                         merged_docs[media_type][path]['num_hits'] = current_hits
                         merged_docs[media_type][path]['match_type'] = current_match_type

                # RRF Math
                if path not in merged_scores[media_type]: merged_scores[media_type][path] = 0.0
                merged_scores[media_type][path] += 1 / (rrf_constant + rank + 1)

        logger.info("Applying RRF")
        for r in text_results: apply_rrf_rank(r, "text")
        for r in image_results: apply_rrf_rank(r, "image")

        # 5. FINALIZE
        final_results = {'text': [], 'image': []}

        for media_type in ['text', 'image']:
            # Sort paths by their final RRF score
            sorted_paths = sorted(merged_scores[media_type].keys(), 
                                key=lambda x: merged_scores[media_type][x], 
                                reverse=True)
            
            for path in sorted_paths[:top_k]:
                doc = merged_docs[media_type][path]
                doc['score'] = merged_scores[media_type][path]
                final_results[media_type].append(doc)

        logger.info("Search concluded.")
        return final_results

    def _embed_query(self, query, negative_query, model):
        """Helper to create the query vector."""
        try:
            # BGE models need specific instructions
            needs_prefix = self.config.get('text_model_name', "") in ["BAAI/bge-small-en-v1.5", "BAAI/bge-large-en-v1.5"]
            prefix = "Represent this sentence for searching relevant passages: " if needs_prefix else ""

            pos_vec = model.encode([prefix + query])[0] if query else None
            neg_vec = model.encode([prefix + negative_query])[0] if negative_query else None

            final_vec = pos_vec
            if pos_vec is not None and neg_vec is not None:
                final_vec = pos_vec - neg_vec
            elif neg_vec is not None:
                final_vec = -neg_vec 
            
            if final_vec is None: return None

            norm = np.linalg.norm(final_vec)
            return final_vec / norm if norm > 0 else final_vec
        except Exception as e:
            logger.error(f"embed_query error: {e}")
            return None