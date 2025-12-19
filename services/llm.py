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
        self.drive_service = None

    def _get_drive_service(self):
        """Lazy-loads and caches the Google Drive service."""
        if self.drive_service is None and self.config.get("use_drive", False):
            self.drive_service = get_drive_service(self.config, logger.info)
        return self.drive_service

    def run(self, job):
        if not self.model.loaded:
            logger.warning("LLM Job attempted while model unloaded.")
            return False

        path_obj = Path(job.path)
        # Safely get model name (e.g. "gpt-4o-mini" or "local-model")
        model_name = getattr(self.model, 'model_name', 'system')

        logger.info(f"[Critic] Analyzing: {path_obj.name}...")
        
        # --- PREPARE CONTEXT & PROMPT ---
        
        is_image = path_obj.suffix.lower() in self.config.get('image_extensions', [])
        is_text = path_obj.suffix.lower() in self.config.get('text_extensions', [])
        has_vision = getattr(self.model, 'vision', False)
        
        image_paths = []
        prompt = ""
        
        # A. IMAGE ANALYSIS
        if is_image:
            if not has_vision:
                logger.warning(f"[Critic] ✗ Skipping image (No Vision support): {path_obj.name}")
                return False

            image_paths = [str(job.path)]
            prompt = (
                "Analyze this image for a personal knowledge base. "
                "Rate 'Information Density' & 'Utility' (0.0 to 1.0). "
                "Do not pass a moral judgement; rate only based on quality. If the author had malicious intent, but produced well-organized and precise writing, give it a high rating. Essentially, do not rate based on intent or feeling. Be unbiased to the max."
                f"\n\nSTATISTICAL TARGET: Avg 0.5."
                "\n\nCRITERIA:"
                "\n- 0.0: Gibberish, blurry, random, difficult to parse, useless."
                "\n- 0.5: Some issues, could be better, could be worse, has pros and cons."
                "\n- 1.0: Clever, clear, attractive, informative, useful."
                "\n\nReply ONLY with the number."
            )
        
        # B. TEXT ANALYSIS
        elif is_text:
            drive_service = self._get_drive_service()
            full_text = get_text_content(path_obj, drive_service, self.config, logger.info)
            
            if not full_text:
                logger.warning(f"[Critic] ✗ Skipping text file (Empty/Unreadable): {path_obj.name}")
                return False
                
            # Limit context window
            MAX_SNIPPET = 4000
            display_snippet = full_text[:MAX_SNIPPET]

            prompt = (
                f"Analyze the text snippet below."
                "Rate 'Information Density' & 'Utility' (0.0 to 1.0). "
                "Do not pass a moral judgement; rate only based on quality. If the author had malicious intent, but produced well-organized and precise writing, give it a high rating. Essentially, do not rate based on intent or feeling. Be unbiased to the max."
                f"\n\nSTATISTICAL TARGET: Avg 0.5."
                f"\n\nCRITERIA:"
                f"\n- 0.0: Gibberish, spam, empty, random, misinformation, useless."
                f"\n- 0.5: Some issues, could be better, could be worse, has pros and cons."
                f"\n- 1.0: Smart, well-organized, precise, insightful, useful."
                f"\n\nReply ONLY with the number."
                f"\n\nSnippet:\n{display_snippet}"
            )

        # C. UNSUPPORTED FILE TYPE
        else:
            logger.warning(f"[Critic] ✗ Skipping unsupported file: {path_obj.name}")
            return False

        # --- INVOKE AND SAVE ---
        try:
            # Invoke LLM
            response = self.model.invoke(prompt, image_paths=image_paths, temperature=0.1)
            
            # Parse Score
            match = re.search(r"0\.\d+|1\.0|0|1", str(response))
            if match:
                score = float(match.group())
                result_content = f"{score:.2f}"
                logger.info(f"[Critic] ✓ Rated {score:.2f}: {path_obj.name}")
                self.db.save_llm_result(job.path, result_content, model_name)
                return True
            else:
                logger.warning(f"[Critic] Parse Error for {path_obj.name}. Response: {response}")
                return False

        except Exception as e:
            logger.error(f"[Critic] Failed for {path_obj.name}: {e}")
            return False