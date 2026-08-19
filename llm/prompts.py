import logging
from typing import Optional, Dict, Any
from jinja2 import Template, TemplateSyntaxError

logger = logging.getLogger(__name__)

class PromptRegistry:
    @staticmethod
    def get(key: str, version: Optional[int] = None):
        """
        Fetch the active prompt for the given key, or a specific version if requested.
        """
        from llm.models import LLMPrompt
        
        qs = LLMPrompt.objects.filter(key=key)
        if version is not None:
            prompt = qs.filter(version=version).first()
        else:
            prompt = qs.filter(is_active=True).first()
            if not prompt:
                # Fallback to the latest version if none is marked active
                prompt = qs.order_by('-version').first()
                
        if not prompt:
            raise ValueError(f"Prompt with key '{key}' and version {version or 'active'} not found in registry.")
        return prompt

    @staticmethod
    def render(key: str, variables: Optional[Dict[str, Any]] = None, version: Optional[int] = None) -> Dict[str, Any]:
        """
        Retrieve, validate, and render a versioned prompt template using the provided variables.
        """
        prompt = PromptRegistry.get(key, version=version)
        vars_dict = variables or {}
        
        try:
            tmpl = Template(prompt.template)
            rendered = tmpl.render(**vars_dict)
        except TemplateSyntaxError as err:
            raise ValueError(f"Jinja2 syntax error compiling template for '{key}': {err}")
        except Exception as err:
            raise ValueError(f"Error rendering template for '{key}': {err}")
            
        return {
            "prompt_id": prompt.id,
            "prompt_key": prompt.key,
            "prompt_version": prompt.version,
            "rendered_prompt": rendered,
            "variables": vars_dict
        }
