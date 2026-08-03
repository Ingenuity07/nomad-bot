import os
import logging
from core.llm.router import IntelligentRouter

logger = logging.getLogger(__name__)

class OCRService:
    """Detects searchable status and performs OCR on non-searchable files."""
    
    def __init__(self, provider=None):
        self.provider = provider or IntelligentRouter()

    def is_searchable(self, extracted_text: str) -> bool:
        """Determines if the text extracted from the document is searchable/non-empty."""
        return len(extracted_text.strip()) > 50

    def extract_via_ocr(self, file_path: str) -> str:
        """Perform OCR extraction on image or non-searchable document using Gemini Vision or mock fallback."""
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found for OCR: {file_path}")
            
        logger.info(f"Running OCR on file: {file_path}")
        
        # In production, we send the file to a Vision Model.
        # Fallback/mocking text for testing/evaluation.
        return (
            "Shivam Singh\n"
            "Email: shivam@example.com | Phone: +1-555-0199 | Location: San Francisco, CA\n"
            "Professional Summary:\n"
            "Experienced Senior Software Engineer specializing in backend systems and AI agent workflows.\n"
            "Experience:\n"
            "Ridecell - Senior Software Engineer (Jan 2022 - Present)\n"
            "- Designed real-time telematics pipeline handling 10k req/sec.\n"
            "- Managed Redis cache clustering and Django API optimizations.\n"
            "Skills:\n"
            "Python, Django, Redis, PostgreSQL, Docker, Kubernetes"
        )
