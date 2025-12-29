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
                logger.warning("✗ LLM Job attempted while model unloaded.")
                return False

            path_obj = Path(job.path)
            model_name = getattr(self.model, 'model_name', 'system')

            # logger.info(f"Analyzing: {path_obj.name}...")
            
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
                prompt = (
                    f"Analyze this image for a search engine index by generating a direct, factual description of the context, followed immediately by a comprehensive list of relevant search keywords, synonyms, and entities. Keep the description dry and robotic, avoiding flowery language or meta-phrases like 'this image depicts,' and instead focus strictly on visible objects, actions, and specific data. Output only the plain text result consisting of the factual description followed by the comma-separated keyword list. Make your response 250 words or less.\n\n"
                    f"Filename: {path_obj.name}"
                )
            
            # B. TEXT ANALYSIS
            elif is_text:
                try:
                    drive_service = get_drive_service(self.config)

                    full_text = get_text_content(Path(job.path), drive_service, self.config)
                    if not full_text:
                        logger.warning(f"✗ No valid text extracted from {path_obj.name}.")
                        return False
                    
                    try:
                        import tiktoken
                        enc = tiktoken.get_encoding("cl100k_base")
                        context_limit = self.config.get('llm_context_length', 4096)
                        tokens = enc.encode(full_text, disallowed_special=())
                        # context_limit is now treated as TOKENS, not characters
                        text = enc.decode(tokens[:context_limit-512])  # Room for prompt + response
                    except Exception as e:
                        logger.error(f"✗ Tokenization error for {path_obj.name}: {e}")
                        text = full_text[:10000]

                except Exception as e:
                    logger.error(f"✗ Failed to get text for LLM from {path_obj.name}: {e}")
                    return False

                prompt = (
                    f"Analyze this document for a search engine index by generating a direct, factual description of the context, followed immediately by a comprehensive list of relevant search keywords, synonyms, and entities. Keep the description dry and robotic, avoiding flowery language or meta-phrases like 'this image depicts,' and instead focus strictly on visible objects, actions, and specific data. Output only the plain text result consisting of the factual description followed by the comma-separated keyword list. Make your response 250 words or less.\n\n"
                    f"Filename: {path_obj.name}. Content: "
                    f"{text}" 
                )

            else:
                logger.warning(f"✗ Skipping unsupported file: {path_obj.name}")
                return False

            # --- INVOKE AND SAVE ---
            response = self.model.invoke(prompt, image_paths=image_paths, attached_image_path=None, temperature=0.3)
            if ("LM Studio Invoke Error" in response) or ("OpenAI Invoke Error" in response):
                logger.error(f"✗ LLM invocation error for {path_obj.name}")
                return False

            cleaned_response = response.strip()
            logger.info(f"✓ LLM response made for {path_obj.name}: {cleaned_response[:50]}...")
            
            self.db.save_llm_result(job.path, cleaned_response, model_name)
                
            return True

        except Exception as e:
            logger.error(f"✗ Failed for {path_obj.name}: {e}")
            return False