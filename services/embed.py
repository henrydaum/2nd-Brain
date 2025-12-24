import logging
import pathlib
import json
from pathlib import Path
import io
# 3rd Party
from PIL import Image
import numpy as np
# Internal
from services.utils import process_text_file, is_gibberish, RecursiveTokenSplitter
from Parsers import get_drive_service

logger = logging.getLogger("EmbedService")

class EmbedService:
    def __init__(self, db, text_model, image_model, config):
        self.db = db
        self.text_model = text_model   # SentenceTransformerEmbedder (e.g., BGE)
        self.image_model = image_model # SentenceTransformerEmbedder (e.g., CLIP)
        self.config = config # Pass config to use parser settings (max_chars, drive, etc.)
        # Initialize Splitter (using config values)
        chunk_size = config.get('chunk_size', 1024)
        chunk_overlap = config.get('chunk_overlap', 64)
        self.text_splitter = RecursiveTokenSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

    def run_batch(self, jobs, batch_type):
        """
        Process a list of jobs.
        Returns: A list of job paths that were successfully embedded.
        """
        successful_paths = []
        
        if batch_type == "text":
            try:
                return self._run_text_batch(jobs)
            except Exception as e:
                logger.error(f"Text Embed Batch failed: {e}")
                return []
        elif batch_type == "image":
            try:
                return self._run_image_batch(jobs)
            except Exception as e:
                logger.error(f"Image Embed Batch failed: {e}")
                return []
        
        return successful_paths

    def _run_text_batch(self, jobs):
        if not self.text_model.loaded:
            logger.warning("Text Embed Batch attempted while model unloaded.")
            return []

        # List of (chunk_index, chunk_text, job_path) tuples
        all_chunks_data = [] 
        # List of text strings for batch encoding
        text_inputs = []

        drive_service = get_drive_service(self.config)

        for job in jobs:
            # Get the parsed and chunked content
            # Returns: list of (index, chunk_text) tuples
            chunked_data = process_text_file(
                pathlib.Path(job.path), 
                drive_service, 
                self.config,
                self.text_splitter
            )
            
            if not chunked_data: continue

            # Map chunks to the batch lists
            for index, chunk_text in chunked_data:
                all_chunks_data.append((index, chunk_text, job.path))
                text_inputs.append(chunk_text)

        if not text_inputs: 
            return []

        # 1. Run Text Model
        try:
            logger.info(f"Embedding {len(text_inputs)} text chunks...")
            embeddings_numpy = self.text_model.encode(text_inputs, batch_size=self.config.get("batch_size", 11))
        except Exception as e:
            logger.error(f"Text embedding batch failed: {e}")
            return []

        if embeddings_numpy is None:
            logger.warning("Text embedding batch failed: no embeddings returned.")
            return []

        # 2. Save Results (Chunk by Chunk)
        successful_paths = set()
        for i, (index, chunk_text, job_path) in enumerate(all_chunks_data):
            try:
                vector_bytes = embeddings_numpy[i].tobytes()
                # Store (index, text_content, embedding_bytes)
                self.db.save_embeddings(
                    job_path, 
                    [(index, chunk_text, vector_bytes, self.text_model.model_name)]
                )
                successful_paths.add(job_path)
            except Exception as e:
                logger.error(f"Save failed for chunk {index} in {job_path}: {e}")

        logger.info(f"Successfully saved {len(all_chunks_data)} text embeddings for {len(successful_paths)} file(s).")
        return list(successful_paths)

    def _run_image_batch(self, jobs):
        if not self.image_model.loaded:
            logger.warning("Image Embed Batch attempted while model unloaded.")
            return []

        image_objects = []
        valid_jobs = []
        
        for job in jobs:
            try:
                # Use PIL to load the image safely
                Image.MAX_IMAGE_PIXELS = None
                with Image.open(job.path).convert("RGBA").convert("RGB") as img:
                    img.thumbnail((512, 512))  # SHOULD BE ABLE TO WORK WITH CLIP
                    # IMPORTANT: Use .copy() to ensure the object persists outside the 'with' block
                    image_objects.append(img.copy()) 
                    valid_jobs.append(job)
            except Exception as e:
                logger.error(f"Failed to load image {job.path}: {e}")

        if not image_objects: return []

        # 1. Run Image Model
        try:
            image_embeddings_numpy = self.image_model.encode(image_objects, batch_size=self.config.get("batch_size", 11))
        except Exception as e:
            logger.error(f"Image embedding batch failed: {e}")
            return []

        if image_embeddings_numpy is None:
            logger.warning("Image embedding batch failed: no embeddings returned.")
            return []

        # 2. Save Results (One embedding per image)
        successful_paths = []
        for i, job in enumerate(valid_jobs):
            try:
                # We use a placeholder for the required text field.
                image_text_placeholder = "[IMAGE]"
                
                vector_bytes = image_embeddings_numpy[i].tobytes()
                
                # Format: [(chunk_index=0, text_content, embedding_bytes)]
                data = [(0, image_text_placeholder, vector_bytes, self.image_model.model_name)]
                
                self.db.save_embeddings(job.path, data)
                successful_paths.append(job.path)
            except Exception as e:
                logger.error(f"Save failed for image {job.path}: {e}")
        
        logger.info(f"Successfully saved {len(successful_paths)} image embeddings.")
        return successful_paths

    def run_embed_llm(self, job):
        if not self.text_model.loaded:
            logger.warning("LLM Embed attempted while model unloaded.")
            return False
        
        try:
            llm_response = self.db.get_llm_result(job.path)

            if not llm_response:
                logger.warning(f"No LLM response to embed: {Path(job.path).name}")
                return False

            queries = [item.lstrip('\'\"0123456789.-• *').rstrip('\'\"') for item in llm_response.splitlines()]
            
            embeddings_numpy = self.text_model.encode(queries, batch_size=self.config['batch_size'])
            if embeddings_numpy is None or len(embeddings_numpy) == 0:
                logger.warning(f"Failed to get llm embedding: {Path(job.path).name}")
                return False
            
            data_batch = []
            for i, query_text in enumerate(queries):
                vector_bytes = embeddings_numpy[i].tobytes()
                data = (-(i+1), query_text, vector_bytes, self.text_model.model_name)  # Use negative indices < 0
                data_batch.append(data)
            self.db.save_embeddings(job.path, data_batch)

            logger.info(f"Successfully saved {len(data_batch)} LLM Embeddings for: {Path(job.path).name}")
            return True
        except Exception as e:
            logger.error(f"Failed to embed LLM response: {Path(job.path).name}: {e}")
            return False