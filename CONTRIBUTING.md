# Contributing

Thanks for your interest in contributing to Second Brain!

## Development Setup

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/henrydaum/2nd-Brain.git
    cd 2nd-Brain
    ```

2.  **Create and activate a virtual environment:**
    ```bash
    python -m venv venv
    # Windows
    .\venv\Scripts\activate
    # Linux/Mac
    source venv/bin/activate
    ```

3.  **Install dependencies:**
    ```bash
    pip install -r requirements.txt
    ```

## Running Tests

We use `unittest` for testing. To run all tests:

```bash
python -m unittest discover tests
```

## Project Structure

-   `main.pyw`: Entry point.
-   `config.py`: Configuration management.
-   `orchestrator.py`: Manages background jobs.
-   `services/`: Business logic for OCR, Embeddings, LLM.
-   `Parsers.py`: File parsing logic.
-   `tests/`: Unit tests.

## Coding Style

-   Please add type hints where possible.
-   Add unit tests for new features.
-   Keep functions small and focused.
