import logging
import re
import json
from pathlib import Path

# Import utilities
from Parsers import get_drive_service
from services.utils import get_text_content

logger = logging.getLogger("LLMService")

class LLMService:
    def __init__(self, db, model, config):
        self.db = db
        self.model = model
        self.config = config

    def run(self, job):
        try:
            if not self.model.loaded:
                logger.warning("LLM Job attempted while model unloaded.")
                return False

            path_obj = Path(job.path)
            model_name = getattr(self.model, 'model_name', 'system')

            logger.info(f"Analyzing: {path_obj.name}...")
            
            # --- PREPARE CONTEXT & PROMPT ---
            
            is_image = path_obj.suffix.lower() in self.config.get('image_extensions', [])
            is_text = path_obj.suffix.lower() in self.config.get('text_extensions', [])
            has_vision = getattr(self.model, 'vision', False)
            
            image_paths = []
            prompt = ""
            
            # A. IMAGE ANALYSIS
            if is_image:
                if not has_vision:
                    logger.warning(f"✗ Skipping image (No Vision support): {path_obj.name}")
                    return False

                image_paths = [str(job.path)]
                prompt = (f"Create a comprehensive, retrieval-optimized summary of this image that captures all key information including main topics, entities (names, dates, places, organizations), and factual details. Use diverse terminology with synonyms and varied phrasings that one might search for, incorporating both technical terms and plain language. Consider the utility and usefulness of the image and what questions the image might help answer. Use plain text with no markdown, and output ONLY the summary. No intro (e.g. 'Here is a summary'). No outro. Aim for one short paragraph under 100 words.\n\n"
                f"Filename: {path_obj.name}"
                )
            
            # B. TEXT ANALYSIS
            elif is_text:
                drive_service = get_drive_service(self.config)

                context_limit = 20000
                text = get_text_content(Path(job.path), drive_service, self.config)[:context_limit]
                if not text:
                    logger.warning("LLM run - no valid text extracted.")
                    return False

                prompt = (f"Create a comprehensive, retrieval-optimized summary of this document that captures all key information including main topics, entities (names, dates, places, organizations), and factual details. Use diverse terminology with synonyms and varied phrasings that one might search for, incorporating both technical terms and plain language. Consider the utility and usefulness of the document and what questions the document might help answer. Use plain text with no markdown, and output ONLY the summary. No intro (e.g. 'Here is a summary'). No outro. Aim for one short paragraph under 100 words.\n\n"
                f"Filename: {path_obj.name}. Content:"
                f"{text}" 
                )

            else:
                logger.warning(f"✗ Skipping unsupported file: {path_obj.name}")
                return False

            # --- INVOKE AND SAVE ---
            # Invoke LLM
            response = self.model.invoke(prompt, image_paths=image_paths, temperature=0.3)
            if ("LM Studio Invoke Error" in response) or ("OpenAI Invoke Error" in response):
                logger.error(f"LLM invocation error for {path_obj.name}")
                return False

            cleaned_response = response.strip()
            logger.info(f"✓ LLM response made for {path_obj.name}: {cleaned_response}")
            
            self.db.save_llm_result(job.path, cleaned_response, model_name)
                
            return True

        except Exception as e:
            logger.error(f"Failed for {path_obj.name}: {e}")
            return False