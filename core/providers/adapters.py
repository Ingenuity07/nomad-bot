from typing import Dict, Any
from core.providers.base import LLMProvider, OCRProvider
from core.llm.router import IntelligentRouter
from core.documents.ocr import OCRService

class RouterLLMAdapter(LLMProvider):
    """Adapter wrapping our IntelligentRouter into the standard LLMProvider interface."""
    
    def __init__(self, router: IntelligentRouter = None):
        self.router = router or IntelligentRouter()
        
    def generate(self, prompt: str, system_prompt: str = None, temperature: float = 0.0) -> Dict[str, Any]:
        return self.router.generate(prompt=prompt, system_prompt=system_prompt)


class VisionOCRAdapter(OCRProvider):
    """Adapter wrapping our OCRService into the standard OCRProvider interface."""
    
    def __init__(self, ocr_service: OCRService = None):
        self.ocr_service = ocr_service or OCRService()
        
    def extract_text(self, file_path: str) -> str:
        return self.ocr_service.extract_via_ocr(file_path)
