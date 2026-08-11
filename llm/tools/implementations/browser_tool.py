import os
from typing import Dict, Any
from playwright.sync_api import sync_playwright
from ..base import BaseTool

class PlaywrightBrowser:
    """Singleton-like manager for Playwright sync browser sessions."""
    _playwright = None
    _browser = None
    _context = None
    _page = None

    @classmethod
    def get_page(cls):
        if cls._page is None or cls._page.is_closed():
            if cls._playwright is None:
                cls._playwright = sync_playwright().start()
            if cls._browser is None:
                cls._browser = cls._playwright.chromium.launch(headless=True)
            if cls._context is None:
                cls._context = cls._browser.new_context(
                    viewport={"width": 1280, "height": 800},
                    user_agent="Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
                )
            cls._page = cls._context.new_page()
            cls._page.set_default_timeout(30000)  # 30 seconds default timeout
        return cls._page

    @classmethod
    def close(cls):
        if cls._page:
            try:
                cls._page.close()
            except Exception:
                pass
            cls._page = None
        if cls._context:
            try:
                cls._context.close()
            except Exception:
                pass
            cls._context = None
        if cls._browser:
            try:
                cls._browser.close()
            except Exception:
                pass
            cls._browser = None
        if cls._playwright:
            try:
                cls._playwright.stop()
            except Exception:
                pass
            cls._playwright = None


def get_screenshot_dir() -> str:
    """Helper to locate the active artifact or project folder to store screenshots."""
    gemini_dir = "/Users/shivamsingh/.gemini/antigravity-ide/brain/26c477f8-7d9f-4ce4-a2eb-e1bc92d5cddd"
    if os.path.exists(gemini_dir):
        return gemini_dir
    
    # Fallback to project root directory / artifacts
    base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))
    project_artifacts = os.path.join(base_dir, "artifacts")
    os.makedirs(project_artifacts, exist_ok=True)
    return project_artifacts


class BrowserTool(BaseTool):
    """Tool to interact with and scrape web pages using Playwright."""

    @property
    def name(self) -> str:
        return "browser_action"

    @property
    def description(self) -> str:
        return (
            "Interact with external websites. Actions available: 'navigate', 'click', 'fill', "
            "'upload_file', 'get_content', and 'screenshot'. For 'navigate', supply 'url'. "
            "For 'click', 'fill', and 'upload_file', supply 'selector'. For 'fill' and 'upload_file', supply 'value'."
        )

    @property
    def parameters(self) -> dict:
        return {
            "type": "object",
            "properties": {
                "action": {
                    "type": "string",
                    "enum": ["navigate", "click", "fill", "upload_file", "get_content", "screenshot"],
                    "description": "The browser action to execute."
                },
                "url": {
                    "type": "string",
                    "description": "The target URL (required for 'navigate')."
                },
                "selector": {
                    "type": "string",
                    "description": "CSS selector or text selector of the target element (required for click/fill/upload)."
                },
                "value": {
                    "type": "string",
                    "description": "Text to type (for 'fill') or local path to file (for 'upload_file')."
                },
                "screenshot_name": {
                    "type": "string",
                    "description": "Filename for saving the screenshot (for 'screenshot'). Example: 'job_form.png'."
                }
            },
            "required": ["action"]
        }

    def execute(self, action: str, url: str = None, selector: str = None, value: str = None, screenshot_name: str = None, **kwargs) -> str:
        try:
            if action == "navigate":
                if not url:
                    return "Error: 'url' parameter is required for navigate action."
                
                # Safety Guard: Block navigating to private/local/metadata networks or file system
                lower_url = url.lower()
                if "169.254.169.254" in lower_url or "localhost" in lower_url or "127.0.0.1" in lower_url or lower_url.startswith("file://") or "::1" in lower_url:
                    return "Error: Navigation to local network addresses or local file system is blocked for safety reasons."

            page = PlaywrightBrowser.get_page()

            if action == "navigate":
                if not (url.startswith("http://") or url.startswith("https://")):
                    url = "https://" + url
                response = page.goto(url)
                page.wait_for_load_state("networkidle", timeout=5000)
                status = response.status if response else "Unknown"
                return f"Successfully navigated to {url} (Status: {status}). Page title: {page.title()}"

            elif action == "get_content":
                title = page.title()
                current_url = page.url
                # Extract text content from body
                text_content = page.locator("body").inner_text()
                
                # Extract form elements and interactive buttons to help LLM reason about inputs
                interactive_elements = page.evaluate("""
                    () => {
                        const items = [];
                        document.querySelectorAll('input, button, select, textarea, a').forEach(el => {
                            const rect = el.getBoundingClientRect();
                            if (rect.width > 0 && rect.height > 0) {
                                // Find closest label text if available
                                let label = '';
                                if (el.id) {
                                    const labelEl = document.querySelector(`label[for="${el.id}"]`);
                                    if (labelEl) label = labelEl.innerText;
                                }
                                items.push({
                                    tag: el.tagName.toLowerCase(),
                                    type: el.type || '',
                                    name: el.name || '',
                                    id: el.id || '',
                                    placeholder: el.placeholder || '',
                                    text: (el.innerText || el.value || '').trim().substring(0, 50),
                                    label: label.trim()
                                });
                            }
                        });
                        return items.slice(0, 50); // limit to 50 items
                    }
                """)
                
                elements_summary = []
                for item in interactive_elements:
                    selector_desc = []
                    if item['id']:
                        selector_desc.append(f"id: '#{item['id']}'")
                    if item['name']:
                        selector_desc.append(f"name: '{item['name']}'")
                    if item['text']:
                        selector_desc.append(f"text: '{item['text']}'")
                    if item['label']:
                        selector_desc.append(f"label: '{item['label']}'")
                    
                    elements_summary.append(
                        f"- <{item['tag']}> type='{item['type']}' ({', '.join(selector_desc)})"
                    )

                result = (
                    f"Current URL: {current_url}\n"
                    f"Page Title: {title}\n\n"
                    f"--- Visible Page Text (truncated) ---\n"
                    f"{text_content[:8000]}\n"
                    f"--- End Page Text ---\n\n"
                    f"--- Interactive Elements Found ---\n"
                    f"{chr(10).join(elements_summary) if elements_summary else 'No interactive elements'}\n"
                )
                return result

            elif action == "click":
                if not selector:
                    return "Error: 'selector' parameter is required for click action."
                
                # Check selector existence
                page.wait_for_selector(selector, timeout=10000)
                page.click(selector)
                try:
                    page.wait_for_load_state("load", timeout=5000)
                except Exception:
                    pass
                return f"Successfully clicked '{selector}'. New Page Title: {page.title()}"

            elif action == "fill":
                if not selector or value is None:
                    return "Error: 'selector' and 'value' parameters are required for fill action."
                page.wait_for_selector(selector, timeout=10000)
                page.fill(selector, value)
                return f"Successfully filled '{selector}' with value."

            elif action == "upload_file":
                if not selector or not value:
                    return "Error: 'selector' and 'value' (file path) parameters are required for upload_file action."
                if not os.path.exists(value):
                    return f"Error: Local file '{value}' does not exist."
                page.wait_for_selector(selector, timeout=10000)
                page.set_input_files(selector, value)
                return f"Successfully uploaded file '{os.path.basename(value)}' to '{selector}'."

            elif action == "screenshot":
                if not screenshot_name:
                    screenshot_name = "browser_screenshot.png"
                if not screenshot_name.endswith(".png"):
                    screenshot_name += ".png"
                screenshot_dir = get_screenshot_dir()
                screenshot_path = os.path.join(screenshot_dir, screenshot_name)
                page.screenshot(path=screenshot_path)
                return f"Successfully saved screenshot as '{screenshot_name}' in artifacts directory."

            else:
                return f"Error: Unknown action '{action}'."

        except Exception as e:
            return f"Error executing browser action '{action}': {str(e)}"
