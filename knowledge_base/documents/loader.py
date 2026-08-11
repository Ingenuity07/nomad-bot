import os
from pypdf import PdfReader
from docx import Document

class DocumentLoader:
    """Loads and extracts raw text from PDF, DOCX, Markdown, or Plain Text files."""
    
    @staticmethod
    def load(file_path: str) -> dict:
        if not os.path.exists(file_path):
            raise FileNotFoundError(f"File not found: {file_path}")
            
        ext = os.path.splitext(file_path)[1].lower()
        text = ""
        metadata = {
            "file_path": file_path,
            "file_size": os.path.getsize(file_path),
            "extension": ext
        }
        
        if ext == ".pdf":
            reader = PdfReader(file_path)
            metadata["pages"] = len(reader.pages)
            pages_text = []
            for page in reader.pages:
                pages_text.append(page.extract_text() or "")
            text = "\n".join(pages_text)
        elif ext == ".docx":
            doc = Document(file_path)
            paragraphs = [p.text for p in doc.paragraphs]
            text = "\n".join(paragraphs)
        elif ext in [".txt", ".md", ".markdown"]:
            with open(file_path, "r", encoding="utf-8") as f:
                text = f.read()
        else:
            raise ValueError(f"Unsupported file format: {ext}")
            
        return {
            "text": text,
            "metadata": metadata
        }
