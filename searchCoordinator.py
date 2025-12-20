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
        system_prompt = config['system_prompt']
        
        # 2. Define the partial context once
        partials = {"system_prompt": system_prompt}

        # 3. Create the templates
        # Assumes your config strings use Python's standard {variable} syntax
        self.rag_prompt = SimpleTemplate(
            "{system_prompt}\n\n{query}\n{attachment_context}\n{database_results}\n**Your Task:**\nBased exclusively on the information provided above, write a concise and helpful response. Your primary goal is to synthesize the information to **guide the user towards what they want**.\n\n**Instructions:**\n- The text search results are **snippets** from larger documents and may be incomplete.\n- Do **not assume or guess** the author of a document unless the source text makes it absolutely clear.\n- The documents don't have timestamps; don't assume the age of a document unless the source text makes it absolutely clear.\n- Cite every piece of information you use from the search results with its source, like so: (source_name).\n- If the provided search results are not relevant to the user's request, state that you could not find any relevant information.\n- Use markdown formatting (e.g., bolding, bullet points) to make the response easy to read.\n- If there are images, make sure to consider them for your response.", 
            partial_vars=partials
        )