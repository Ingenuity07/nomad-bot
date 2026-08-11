import os
from ..base import BaseTool

class FileTool(BaseTool):
    """Tool to read files from the local filesystem."""
    
    @property
    def name(self) -> str:
        return "read_file"
        
    @property
    def description(self) -> str:
        return "Read the contents of a local file."
        
    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "file_path": {
                    "type": "string",
                    "description": "Absolute or relative path to the file."
                }
            },
            "required": ["file_path"]
        }
        
    def execute(self, file_path: str, **kwargs) -> str:
        try:
            if not os.path.exists(file_path):
                return f"Error: File {file_path} does not exist."
            with open(file_path, 'r', encoding='utf-8') as f:
                return f.read()
        except Exception as e:
            return f"Error reading file: {str(e)}"
