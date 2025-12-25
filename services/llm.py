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
                prompt = (f"Analyze the following image for a search database. Start your analysis with 2-3 search queries/questions that would be used to find this image. After that, provide a brief summary of the image, making sure to note keywords and main topics. Use plain text with no markdown, and output ONLY the analysis. No intro (e.g. 'Here is an analysis'). No outro. Keep it under 150 words.\n\n"
                f"Filename: {path_obj.name}"

                    # f"Write a list of 3 diverse google search queries/questions that would be used to find this image (filename: {path_obj.name}). Use plain text only. Do not use numbering, bullet points, or markdown. Output ONLY the queries. No intro (e.g. 'Here is a list'). No outro. Write each query on a new line.\n"
                )
            
            # B. TEXT ANALYSIS
            elif is_text:
                drive_service = get_drive_service(self.config)

                context_limit = 10000
                text = get_text_content(Path(job.path), drive_service, self.config)[:context_limit]
                if not text:
                    logger.warning("LLM run - no valid text extracted.")
                    return False

                prompt = (f"Analyze the following document for a search database. Start your analysis with 2-3 search queries/questions that would be used to find this document. After that, provide a brief summary of the doc, making sure to note keywords and main topics. Use plain text with no markdown, and output ONLY the analysis. No intro (e.g. 'Here is an analysis'). No outro. Keep it under 150 words.\n\n"
                f"Filename: {path_obj.name}. Content:"
                f"{text}"

                    # f"Write a list of 7 diverse google search queries/questions that are answered by the text below (filename: {path_obj.name}). Use plain text only. Do not use numbering, bullet points, or markdown. Output ONLY the queries. No intro (e.g. 'Here is a list'). No outro. Write each query on a new line.\n\n"
                    # f"{text}\n"
                )  # 7 is a lucky number

            else:
                logger.warning(f"✗ Skipping unsupported file: {path_obj.name}")
                return False

            # --- INVOKE AND SAVE ---
            # Invoke LLM
            response = self.model.invoke(prompt, image_paths=image_paths, temperature=0.3)
            if "LM Studio Invoke Error" in response:
                logger.error(f"LLM invocation error for {path_obj.name}")
                return False

            cleaned_response = response.strip()
            logger.info(f"✓ LLM response made for {path_obj.name}: {cleaned_response}")
            
            self.db.save_llm_result(job.path, cleaned_response, model_name)
                
            return True

        except Exception as e:
            logger.error(f"Failed for {path_obj.name}: {e}")
            return False