import logging
import re
from pathlib import Path

# Import utilities
try:
    from Parsers import get_drive_service
    from services.utils import get_text_content
except ImportError:
    # Fallback to prevent immediate crash if utils aren't ready
    def get_drive_service(config, logger): return None
    def get_text_content(file_path, drive_service, config, logger): return ""

logger = logging.getLogger("LLMService")

class LLMService:
    def __init__(self, db, model, config):
        self.db = db
        self.model = model
        self.config = config

    def run(self, job):
        if not self.model.loaded:
            logger.warning("LLM Job attempted while model unloaded.")
            return False

        path_obj = Path(job.path)
        # Safely get model name (e.g. "gpt-4o-mini" or "local-model")
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
            prompt = (
                "Create a concise yet holistic summary of the image provided."
                "Use plaintext only, no markdown or special formatting."
                "Make sure to include keywords and main topics."
                "YOUR SUMMARY:"
            )
        
        # B. TEXT ANALYSIS
        elif is_text:
            drive_service = get_drive_service(self.config)
            full_text = get_text_content(path_obj, drive_service, self.config)
            
            if not full_text:
                logger.warning(f"✗ Skipping text file (Empty/Unreadable): {path_obj.name}")
                return False
                
            # Limit context window
            MAX_SNIPPET = 4000
            display_snippet = full_text[:MAX_SNIPPET]

            prompt = (
                "Create a concise yet holistic summary of the following content:\n\n"
                f"{display_snippet}\n\n"
                "Use plaintext only, no markdown or special formatting."
                "Make sure to include keywords and main topics."
                "YOUR SUMMARY:"
            )

        # C. UNSUPPORTED FILE TYPE
        else:
            logger.warning(f"✗ Skipping unsupported file: {path_obj.name}")
            return False

        # --- INVOKE AND SAVE ---
        try:
            # Invoke LLM
            response = self.model.invoke(prompt, image_paths=image_paths, temperature=0.1)
            if "LM Studio Invoke Error" in response:
                logger.error(f"LLM Invoke Error for {path_obj.name}")
                return False

            result_content = response.strip()
            logger.info(f"✓ LLM summary made for {path_obj.name}: {result_content[:300]}...")
            self.db.save_llm_result(job.path, result_content, model_name)
            return True

        except Exception as e:
            logger.error(f"Failed for {path_obj.name}: {e}")
            return False