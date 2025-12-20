import os
from pathlib import Path
from dataclasses import dataclass, field

@dataclass
class SearchFacts:
    """Holds all data for a single user request and its results."""
    from typing import List, Optional, Any, Dict
    from PIL import Image
    
    # --- Core Inputs (from user) ---
    query: str
    attachment: Optional[str] = None
    attachment_path: Optional[Path] = None
    
    # --- Processed Attachment Data ---
    attachment_chunks: List[str] = field(default_factory=list)
    attachment_context_string: str = ""
    attachment_name: str = ""
    attachment_folder: str = ""

    # --- Image Attachment Specifics ---
    attached_image: Optional[Image.Image] = None
    attached_image_path: str = ""
    attached_image_description: str = ""
    
    # --- Search Terms ---
    lexical_search_term: str = ""

    # --- Results (from backend) ---
    # This is the raw List[Dict] from hybrid_search
    image_search_results: List[Dict[str, Any]] = field(default_factory=list)
    text_search_results: List[Dict[str, Any]] = field(default_factory=list)
    
    # This is a convenience list derived from image_search_results
    image_paths: List[str] = field(default_factory=list)

    # --- State ---
    current_state: str = None
    image_path_being_evaluated = ""

    final_prompt: str = ""

class SimpleTemplate:
    """A lightweight wrapper to mimic LangChain's partial formatting."""
    def __init__(self, template_str, partial_vars=None):
        self.template_str = template_str
        self.partial_vars = partial_vars or {}

    def format(self, **kwargs):
        # Merge the pre-filled variables (system_prompt) with the new ones
        merged_args = {**self.partial_vars, **kwargs}
        return self.template_str.format(**merged_args)

class Prompter:
    """Manages the creation of all complex LLM prompts."""
    def __init__(self, config: dict):
        # 1. Prepare the common variable
        system_prompt = config['system_prompt'] + config.get('special_instructions', 'None')
        
        # 2. Define the partial context once
        partials = {"system_prompt": system_prompt}

        # 3. Create the templates
        # Assumes your config strings use Python's standard {variable} syntax
        self.synthesize_results = SimpleTemplate(
            config['synthesize_results_prompt'], 
            partial_vars=partials
        )