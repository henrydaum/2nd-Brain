import os
import json
import logging
from pathlib import Path

logger = logging.getLogger("Config")

class Config:
    DEFAULT_CONFIG = {
        "sync_directories": [
            "Z:\\My Drive",
            "Screenshots" # Will be resolved relative to DATA_DIR if needed, but keeping as string for now
        ],
        "batch_size": 16,
        "chunk_size": 1024,
        "chunk_overlap": 64,
        "flush_timeout": 5.0,
        "max_workers": 6,
        "ocr_backend": "Windows",
        "embed_backend": "Sentence Transformers",
        "text_model_name": "BAAI/bge-small-en-v1.5",
        "image_model_name": "clip-ViT-B-32",
        "llm_backend": "LM Studio",
        "lms_model_name": "gemma-3-4b-it@q4_k_s",
        "openai_model_name": "gpt-4.1",
        "use_drive": True,
        "num_results": 30,
        "text_extensions": [".txt", ".md", ".pdf", ".docx", ".gdoc"],
        "image_extensions": [".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".ico"],
        "use_cuda": True,
        "screenshot_interval": 15,
        "screenshot_folder": "Screenshots",
        "delete_screenshots_after": 9
    }

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.config_path = data_dir / "config.json"
        self._settings = self._load()

    def _load(self):
        """Loads configuration from a JSON file, creating a default one if missing."""
        if not self.config_path.exists():
            logger.info(f"Config file not found. Creating default at: {self.config_path}")

            # Update default screenshots path to be absolute based on data_dir
            defaults = self.DEFAULT_CONFIG.copy()
            defaults["sync_directories"] = [
                "Z:\\My Drive",
                str(self.data_dir / "Screenshots")
            ]

            try:
                with open(self.config_path, 'w') as config_file:
                    json.dump(defaults, config_file, indent=4)
                return defaults
            except OSError as e:
                logger.error(f"Error creating config file: {e}")
                return defaults # Fallback to using defaults in memory

        try:
            with open(self.config_path, 'r') as config_file:
                return json.load(config_file)
        except json.JSONDecodeError:
            logger.error(f"Error: {self.config_path} is corrupted. Loading defaults.")
            defaults = self.DEFAULT_CONFIG.copy()
            defaults["sync_directories"] = [
                "Z:\\My Drive",
                str(self.data_dir / "Screenshots")
            ]
            return defaults

    def get(self, key, default=None):
        return self._settings.get(key, default)

    def __getitem__(self, key):
        return self._settings[key]

    def __setitem__(self, key, value):
        self._settings[key] = value
        self.save()

    def save(self):
        try:
            with open(self.config_path, 'w') as config_file:
                json.dump(self._settings, config_file, indent=4)
        except OSError as e:
            logger.error(f"Error saving config file: {e}")
