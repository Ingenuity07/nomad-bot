import os
import base64
import requests
from ..base import BaseTool
from .browser_tool import get_screenshot_dir

class AnalyzeScreenshotTool(BaseTool):
    """Tool to analyze page screenshot images utilizing Gemini vision API."""

    @property
    def name(self) -> str:
        return "analyze_screenshot"

    @property
    def description(self) -> str:
        return (
            "Analyze a screenshot image file saved in the artifacts directory. "
            "Use this tool to read/verify form validations, inspect UI pages, or debug visual errors on web pages."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "screenshot_name": {
                    "type": "string",
                    "description": "The name of the screenshot file (e.g. 'browser_screenshot.png')."
                },
                "query": {
                    "type": "string",
                    "description": "What you want to analyze or find in the screenshot (e.g. 'Look for red validation errors or error texts')."
                }
            },
            "required": ["screenshot_name", "query"]
        }

    def execute(self, screenshot_name: str, query: str, **kwargs) -> str:
        try:
            screenshot_dir = get_screenshot_dir()
            file_path = os.path.join(screenshot_dir, screenshot_name)
            if not os.path.exists(file_path):
                return f"Error: Screenshot file '{screenshot_name}' does not exist in artifacts directory."

            # Read and encode image in base64
            with open(file_path, "rb") as image_file:
                image_data = base64.b64encode(image_file.read()).decode("utf-8")

            # Resolve Gemini API Key from environment (fallback to project secret)
            api_key = os.environ.get("GEMINI_API_KEY", "AQ.Ab8RN6Iv9rXDPDXtsa4xv69CfapI_zWl3uGCUe-n3qmzZ0xt4Q")
            model = "gemini-2.5-flash"
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}"

            payload = {
                "contents": [{
                    "parts": [
                        {"text": query},
                        {
                            "inlineData": {
                                "mimeType": "image/png",
                                "data": image_data
                            }
                        }
                    ]
                }]
            }
            
            headers = {"Content-Type": "application/json"}
            response = requests.post(url, headers=headers, json=payload)
            response.raise_for_status()
            
            data = response.json()
            try:
                output_text = data['candidates'][0]['content']['parts'][0]['text']
                return f"Screenshot analysis result:\n{output_text}"
            except (KeyError, IndexError):
                return "Error: Unexpected response format from Gemini API during screenshot analysis."

        except Exception as e:
            return f"Error analyzing screenshot: {str(e)}"
