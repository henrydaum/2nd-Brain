<img width="2481" height="3508" alt="second brain final (image)" src="https://github.com/user-attachments/assets/33c859c1-52da-457c-ab24-7fca065796f7" />

Compared to my old version, [github.com/henrydaum/second-brain](https://github.com/henrydaum/second-brain) and [henrydaum.site](henrydaum.site), this version is more maintainable, 6x faster, and has other great features including OCR.

# Second Brain

<mark>Second Brain is a multimodal userspace operating system that transforms your static local storage into a high-performance, private general intelligence hub.</mark> Running quietly in your system tray, it uses robust multithreading and SQL to sync your files and screen history as they happen, creating a fully searchable on-device knowledge base.

Powered by pre-loaded AI models, Second Brain delivers nearly instantaneous results using both keyword (content) and semantic (meaning) search. With support for over 49 text and image extensions and deep integration for vision-enabled LLMs, it gives you the power to index, analyze, and instantly access your digital world—augmenting your knowledge like a second brain.

## Features

### Hybrid Search ▲ ▲
Combines **Lexical Search** (exact keyword matching via SQLite FTS5) with **Semantic Search** (meaning-based matching via SentenceTransformers). This allows you to find an "invoice from last week" even if you don't remember the file name.
* **How to use:** Type naturally into the search bar. You can use specific keywords (e.g., `invoice_2024.pdf`) or natural language descriptions (e.g., `"the blue logo design we rejected"`). Your files have to be processed using OCR and embedding models before they can be available for search. After indexing, lexical search is available anytime and semantic search is available so long as the models are loaded.

### "The Lens" (Passive Screen Capture) ▼ ▼
An optional background service that periodically captures your screen activity, extracts text using Windows native OCR, and creates a searchable timeline of your digital day.
* **How to use:** Right-click the **System Tray icon** and select **Start Screen Capture** (or toggle it in the Settings tab). The app will silently record your screen at the interval set in your config (default: 15s), and save the photos into a "Screenshots" Data folder, accessible in Settings. To automatically index the screenshots, add the path to this folder to your "Sync Directories", also in Settings. To run a search on this folder specifically, click on the filter button in the search bar and navigate to the folder, then do a normal search.

### Universal Indexing ◄ ►
Parses and indexes numerous folders with a wide variety of formats including PDF, DOCX, PPTX, code files (`.py`, `.js`, etc.), images (`.png`, `.jpg`), and even Google Drive shortcuts (`.gdoc`) *(the full list is available below)*.
* **How to use:** Go to the **Settings** tab (or edit `config.json`) and add your desired folder paths to the `sync_directories` list, and add the extensions you want to `text_extensions` and `image_extensions`. Upon restart, the app will immediately begin scanning these locations for those extensions.

### Real-Time "Watchdog" ◄ ►
The system monitors your folders for changes. If you add, delete, or edit a file, the index updates instantly without requiring a full manual rescan.
* **How to use:** Fully automatic. As long as the application is running (even in the tray), your index remains up to date. If the OCR, Embedding, or LLM models are loaded, they will automatically process the files to enable search. You can add or remove folders and extensions as needed.

### Bot Analysis
When the LLM is loaded, all text and search results will be accompanied by an LLM response called 'AI Insights'. Furthermore, the LLM will automatically give a rating to every file in your database. This rating is based on the overall quality. Files with higher scores are then boosted in the search algorithm, ensuring results are of the highest quality.
* **How to use:** Happens automatically if the LLM is loaded. In order to get feedback on images, load a vision-enabled model, like Gemma 3 or GPT 4.1. AI models can be loaded locally using LM Studio or in the cloud using the OpenAI API (requires key). Once an LLM rating job is done, the rating will be used to improve subsequent results even if the LLM is no longer loaded.

## Screenshots

<img width="1926" height="1260" alt="Screenshot 2025-12-19 130548" src="https://github.com/user-attachments/assets/4469f6c9-c2f0-41a7-b08e-4af07003435e" />
<img width="397" height="407" alt="Screenshot 2025-12-19 131130" src="https://github.com/user-attachments/assets/a6068881-c96d-4259-9ece-67716a92d722" />

## START: Installation

### Prerequisites
- Python 3.10 or higher
- Windows 10/11 (Required for the native Windows OCR engine)

### Setup
1. **Clone the repository:**
   ```bash
   git clone https://github.com/henrydaum/2nd-Brain.git
   cd 2nd-Brain
   ```
2. **Create and activate a virtual environment:**
   ```bash
   python -m venv venv
   .\venv\Scripts\activate
   ```
3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```
   NOTE: this installs PyTorch as ```torch```, which is the CPU-only version. If you want to use GPU, you will need to install PyTorch manually based on your CUDA Toolkit version: [https://pytorch.org/get-started/locally/](https://pytorch.org/get-started/locally/).
   
5. **Run the application:**
   ```bash
   python main.pyw
   ```

## Configuration

The application generates a config.json file in the %LOCALAPPDATA%/2nd Brain/ directory upon the first run. You can modify this file directly or use the Settings tab in the interface.

| Setting | Description | Valid Inputs |
| :--- | :--- | :--- |
| `sync_directories` | A list of local folder paths or drive letters to monitor and index. | List of strings (e.g., `["C:\\Users\\..."]`) |
| `batch_size` | The number of files to process simultaneously for embeddings. | Integer (e.g., `8`, `16`, `32`) |
| `chunk_size` | The maximum number of characters per text chunk when splitting documents (not tokens). | Integer (e.g., `512`, `1024`) |
| `chunk_overlap` | The number of characters to overlap between text chunks to preserve context (not tokens). | Integer (e.g., `64`, `128`) |
| `flush_timeout` | Time in seconds to wait before forcing a batch to process, even if incomplete. | Float (e.g., `5.0`) |
| `max_workers` | The maximum number of background threads used by the Orchestrator. Use the maximum your computer can handle. | Integer (e.g., `4`, `6`, `12`) |
| `ocr_backend` | The OCR engine to use. Currently, only the native Windows 10/11 engine is fully implemented. | `"Windows"` |
| `embed_backend` | The source used for generating vector embeddings. Right now, there is only one source implemented. | `"Sentence Transformers"` |
| `text_model_name` | The HuggingFace model ID used for embedding text documents. | String (e.g., `"BAAI/bge-small-en-v1.5"`, `"BAAI/bge-large-en-v1.5"`, and `"BAAI/bge-m3"`—these have been extensively tested and work well; bge-m3 is multilingual.) |
| `image_model_name` | The CLIP model used for embedding images. | String (e.g., `"clip-ViT-B-32"`, `"clip-ViT-B-16"`, and `"clip-ViT-L-14"`—these have been extensively tested and work well.) |
| `llm_backend` | The service provider for the LLM analysis tasks. | `"LM Studio"`, `"OpenAI"` |
| `lms_model_name` | The model identifier to request when connecting to a local LM Studio server. In order for this to work, LM Studio must be running in the background with the model with this name pre-downloaded. | String (e.g., `"gemma-3-4b-it"`) |
| `openai_model_name` | The model identifier to use if OpenAI backend is selected. | String (e.g., `"gpt-4o"`, `"gpt-3.5-turbo"`) |
| `use_drive` | Enables or disables the Google Drive API integration. To download Google Drive files, you need to make a project in Google Cloud and get a credentials.json file. Place credentials.json in the local AppData folder (shortcut in Settings). | `true`, `false` |
| `quality_weight` | How much the "Quality" score (from the LLM critic) impacts the final ranking. | Float `0.0` - `1.0` |
| `mmr_lambda` | Controls diversity in results. Higher values prioritize relevance; lower values prioritize diversity. | Float `0.0` - `1.0` |
| `mmr_alpha` | Controls the balance between Semantic (1.0) and Lexical (0.0) search results. | Float `0.0` - `1.0` |
| `num_results` | The maximum number of search results to display. | Integer (e.g., `20`, `50`) |
| `text_extensions` | File extensions treated as text documents. Every extension here has a parser in Parsers.py, but more can be written. | List of strings (all valid: `[".txt", ".md", ".pdf", ".docx", ".doc", ".gdoc", ".rtf", ".pptx", ".csv", ".xlsx", ".xls", ".json", ".yaml", ".yml", ".xml", ".ini", ".toml", ".env", ".py", ".js", ".jsx", ".ts", ".tsx", ".html", ".css", ".c", ".cpp", ".h", ".java", ".cs", ".php", ".rb", ".go", ".rs", ".sql", ".sh", ".bat", ".ps1"]`) |
| `image_extensions` | File extensions treated as images. Every extension written here has a parser in Parsers.py. | List of strings (all valid: `[".png", ".jpg", ".jpeg", ".gif", ".webp", ".heic", ".heif", ".tif", ".tiff", ".bmp", ".ico"]`) |
| `use_cuda` | Enables GPU acceleration for embeddings/OCR if available. | `true`, `false` |
| `screenshot_interval`| The delay in seconds between automatic screen captures. | Integer (e.g., `15`, `60`) |
| `screenshot_folder` | Custom path to save screenshots. If empty, defaults to internal AppData folder. | String (Path) or `""` |
| `delete_screenshots_after` | The number of days to retain screenshots before auto-deletion. | Integer (e.g., `7`, `30`) |

## Architecture

- **main.pyw**: Application entry point. Handles initialization of the database, configuration loading, and model initialization.
- **gui.py**: The frontend interface built with PySide6. Manages user interaction and displays search results.
- **orchestrator.py**: Manages background tasks. Uses a priority queue and thread pool to handle OCR, embedding generation, and LLM analysis without blocking the UI.
- **database.py**: Handles all SQLite interactions. Manages the tasks table for file tracking, embeddings for vector storage, and a virtual table for lexical search.
- **watcher.py**: Implements watchdog observers to detect file system events and submit tasks to the orchestrator, enabling live sync.
- **search.py**: Contains logic for the hybrid lexical/semantic search algorithm, including MMR reranking.

## Technical Notes
You can avoid importing PyTorch and Sentence Transformers by creating a compatible class in `embedClass.py` for embeddings from LM Studio, OpenAI, or Gemini APIs. Similarly, new classes can be created for different OCR or LLM models. 

Aside from the PySide6-based GUI, the application is lightweight and primarily uses Python-native libraries, with a few exceptions like Pillow, Numpy, requests, and watchdog. This makes it easy to maintain.

Increasing `max_workers` in `config.json` maximizes thread usage, enabling GPUs to process embeddings for tens of thousands of files per hour. This multithreading, combined with `orchestrator.py`'s SQL idempotency, ensures speedy processing without data loss or redundancy.

## Coming Soon

In order to enable RAG, I plan to expose an API endpoint for the search algorithm. This would enable LLMs trained for tool use to query the entire database from anywhere on your computer. I'll also make a tab next to the text and image results to make room for an LLM response. I apologize I don't have that done already.

## License

This project is licensed under the MIT License.































