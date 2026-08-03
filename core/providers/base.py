from typing import Protocol, Dict, Any

class LLMProvider(Protocol):
    """Abstract protocol defining the required interface for all text generation models."""
    def generate(self, prompt: str, system_prompt: str = None, temperature: float = 0.0) -> Dict[str, Any]:
        ...

class OCRProvider(Protocol):
    """Abstract protocol defining the interface for document OCR extractors."""
    def extract_text(self, file_path: str) -> str:
        ...
