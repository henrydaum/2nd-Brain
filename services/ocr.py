import logging

logger = logging.getLogger("OCRService")

class OCRService:
    def __init__(self, db, model):
        self.db = db
        self.model = model 

    def run(self, job):
        if not self.model.loaded:
            logger.warning("OCR Job attempted while model unloaded.")
            return False

        # logger.info(f"Scanning image: {job.path}")

        # 1. Run Model
        text = self.model.process_image(job.path)
        
        # 2. Save Result
        if text and text.strip():
            self.db.save_ocr_result(job.path, text, self.model.model_name)
            logger.info(f"✓ OCR extracted {len(text)} chars from {job.path}")
        else:
            # FIX: We save an empty result so the DB knows we checked this file.
            # We do NOT mark the task as DONE here; the Orchestrator handles that.
            self.db.save_ocr_result(job.path, " ", self.model.model_name)
            logger.info(f"OCR found no text in {job.path}")

        return True