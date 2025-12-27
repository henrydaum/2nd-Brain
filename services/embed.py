import logging
import pathlib
import json
from pathlib import Path
import io
# 3rd Party
from PIL import Image
import numpy as np
# Internal
from services.utils import process_text_file, is_gibberish, RecursiveCharacterSplitter
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
        self.text_splitter = RecursiveCharacterSplitter(chunk_size=chunk_size, chunk_overlap=chunk_overlap)

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
                logger.error(f"✗ Text Embed Batch failed: {e}")
                return []
        elif batch_type == "image":
            try:
                return self._run_image_batch(jobs)
            except Exception as e:
                logger.error(f"✗ Image Embed Batch failed: {e}")
                return []
        
        return successful_paths

    def _run_text_batch(self, jobs):
        if not self.text_model.loaded:
            logger.warning("✗ Text Embed Batch attempted while model unloaded.")
            return []

        # List of (chunk_index, chunk_text, job_path) tuples
        all_chunks_data = [] 
        # List of text strings for batch encoding
        text_inputs = []

        drive_service = get_drive_service(self.config)

        for job in jobs:
            try:
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
            except Exception as e:
                logger.error(f"✗ Failed to get text chunks for {job.path}: {e}")

        if not text_inputs: 
            return []

        # 1. Run Text Model
        try:
            # logger.info(f"Embedding {len(text_inputs)} text chunks...")
            embeddings_numpy = self.text_model.encode(text_inputs, batch_size=self.config.get("batch_size", 11))
        except Exception as e:
            logger.error(f"✗ Text embedding batch failed: {e}")
            return []

        if embeddings_numpy is None:
            logger.warning("✗ Text embedding batch failed: no embeddings returned.")
            return []

        # 2. Save Results (Chunk by Chunk)
        successful_paths = set()  # Set because multiple chunks per file
        data_list = []
        try:
            for i, (index, chunk_text, job_path) in enumerate(all_chunks_data):
                vector_bytes = embeddings_numpy[i].tobytes()
                data = (job_path, index, chunk_text, vector_bytes, self.text_model.model_name)
                data_list.append(data)
                successful_paths.add(job_path)

            # Commit
            self.db.save_embeddings(data_list)

        except Exception as e:
            logger.error(f"✗ Text batch embed save failed for {job_path}: {e}")
            return []

        logger.info(f"✓ Successfully saved {len(all_chunks_data)} text embeddings for {len(successful_paths)} file(s).")
        return list(successful_paths)

    def _run_image_batch(self, jobs):
        if not self.image_model.loaded:
            logger.warning("✗ Image Embed Batch attempted while model unloaded.")
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
                logger.error(f"✗ Failed to load image {job.path}: {e}")

        if not image_objects: return []

        # 1. Run Image Model
        try:
            image_embeddings_numpy = self.image_model.encode(image_objects, batch_size=self.config.get("batch_size", 11))
        except Exception as e:
            logger.error(f"✗ Image embedding batch failed: {e}")
            return []

        if image_embeddings_numpy is None:
            logger.warning("✗ Image embedding batch failed: no embeddings returned.")
            return []

        # 2. Save Results (One embedding per image)
        successful_paths = []
        data_list = []
        try:
            for i, job in enumerate(valid_jobs):
                # Use a placeholder for the required text field.
                image_text_placeholder = " "
                vector_bytes = image_embeddings_numpy[i].tobytes()
                data = (job.path, 0, image_text_placeholder, vector_bytes, self.image_model.model_name)
                data_list.append(data)                
                successful_paths.append(job.path)

            # Commit
            self.db.save_embeddings(data_list)

        except Exception as e:
            logger.error(f"✗ Image batch embed save failed for {job.path}: {e}")
            return []
        
        logger.info(f"✓ Successfully saved {len(successful_paths)} image embeddings.")
        return successful_paths

    def run_embed_llm(self, job):
        if not self.text_model.loaded:
            logger.warning("✗ LLM Embed attempted while model unloaded.")
            return False
        
        try:
            llm_response = self.db.get_llm_result(job.path)

            if not llm_response:
                logger.warning(f"✗ No LLM response to embed: {Path(job.path).name}")
                return False
            
            embeddings_numpy = self.text_model.encode([llm_response], batch_size=self.config['batch_size'])
            
            if embeddings_numpy is None or len(embeddings_numpy) == 0:
                logger.warning(f"✗ Failed to get llm embedding: {Path(job.path).name}")
                return False
            
            vector_bytes = embeddings_numpy[0].tobytes()
            data = (job.path, -1, llm_response, vector_bytes, self.text_model.model_name)  # Use negative indices < 0
            self.db.save_embeddings([data])

            logger.info(f"✓ Successfully saved LLM Embedding for: {Path(job.path).name}")
            return True
        except Exception as e:
            logger.error(f"✗ Failed to embed LLM response: {Path(job.path).name}: {e}")
            return False