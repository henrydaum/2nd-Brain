<img width="2481" height="3508" alt="second brain final (image)" src="https://github.com/user-attachments/assets/33c859c1-52da-457c-ab24-7fca065796f7" />

# Second Brain

A local, privacy-focused search engine and activity tracker for Windows.
This application indexes local files and screen activity to enable hybrid search (keyword and semantic) without sending data to the cloud. All processing, including embeddings, OCR, and database management, is performed locally on the device.

## Features

* **Hybrid Search**: Utilizes SQLite FTS5 for lexical search and SentenceTransformers for semantic search, allowing retrieval by file content or meaning.
* **Passive Screen Capture**: An optional background service that captures screen activity at configurable intervals. Images are processed via OCR to make visual history text-searchable.
* **Universal Indexing**: Supports parsing and indexing of PDF, DOCX, PPTX, text/code files, images, and Google Drive shortcuts (.gdoc).
* **Local Architecture**: Built on SQLite and local Python libraries. No external telemetry or cloud storage is required.
* **Real-Time Monitoring**: Monitors configured directories for file changes, additions, or deletions and updates the index dynamically.

## Installation

### Prerequisites
* Python 3.10 or higher
* Windows 10/11 (Required for the native Windows OCR engine)

### Setup
1. Clone the repository:
  ```git clone [https://github.com/henrydaum/2nd-Brain.git](https://github.com/henrydaum/2nd-Brain.git)```
  ```cd 2nd-Brain```
2. Create and activate a virtual environment:
  ```python -m venv venv```
  ```.\venv\Scripts\activate```
3. Install dependencies:
  ```pip install -r requirements.txt```
4. Run the application:
  ```python main.pyw```

### Configuration
The application generates a config.json file in the %LOCALAPPDATA%/2nd Brain/ directory upon the first run. You can modify this file directly or use the Settings tab in the interface.

Key configuration options:
- ```sync_directories```: A list of file paths to index.
- ```screenshot_interval```: Time in seconds between screen captures (Default: 15).
- ```delete_screenshots_after```: Retention period for screenshots in days.
- ```llm_backend```: Select between "LM Studio" (local) or "OpenAI".
- ```use_drive```: Boolean to enable or disable Google Drive indexing.

### Architecture
- **main.pyw**: Application entry point. Handles initialization of the database, configuration loading, and service startup.
- **gui.py**: The frontend interface built with PySide6. Manages user interaction and displays search results.
- **orchestrator.py**: Manages background tasks. Uses a priority queue and thread pool to handle OCR, embedding generation, and LLM analysis without blocking the UI.
- **database.py**: Handles all SQLite interactions. Manages the tasks table for file tracking, embeddings for vector storage, and a virtual table for full-text search.
- **watcher.py**: Implements watchdog observers to detect file system events and submit tasks to the orchestrator.
- **search.py**: Contains logic for lexical, semantic, and hybrid search algorithms, including MMR reranking.

### License
This project is licensed under the MIT License.
