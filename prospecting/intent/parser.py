import json
import logging
from typing import Dict, Any, List
from llm.router import IntelligentRouter, llm_service
from llm.enums import LLMOperation, LLMComplexity
from llm.contracts import LLMRequest
from prospecting.intent.schemas import IntentParseResult, ProspectingSpecification, Provenance
from prospecting.intent.prompts import INTENT_PARSER_SYSTEM_PROMPT, PROMPT_VERSION, SCHEMA_VERSION
from prospecting.intent.validator import ProspectingSpecificationValidator
from prospecting.intent.clarifier import ProspectingIntakeClarifier

logger = logging.getLogger(__name__)

class IntentParserError(Exception):
    """Exception raised when intent parsing fails completely after retries."""
    pass

class ProspectingIntentParser:
    """Interprets user natural-language inputs using the IntelligentRouter and returns a validated IntentParseResult."""

    def __init__(self):
        self.router = IntelligentRouter()

    def parse_intent(self, objective: str, target: str = "", qualification: str = "", clarification_history: List[Dict[str, str]] = None) -> IntentParseResult:
        user_input_block = f"1. WHAT USER IS TRYING TO ACHIEVE:\n{objective}\n\n"
        if target:
            user_input_block += f"2. WHO/WHAT USER IS LOOKING FOR:\n{target}\n\n"
        if qualification:
            user_input_block += f"3. WHAT MAKES A GOOD MATCH:\n{qualification}\n\n"

        if clarification_history:
            user_input_block += "### CLARIFICATION QUESTIONS & ANSWERS HISTORY:\n"
            for item in clarification_history:
                q = item.get("question", "")
                a = item.get("answer", "")
                user_input_block += f"Q: {q}\nA: {a}\n---\n"

        prompt = f"Parse the following natural language campaign input into the requested JSON schema:\n\n{user_input_block}"

        # Firing generic LLM Request
        req = LLMRequest(
            operation=LLMOperation.STRUCTURED_OUTPUT,
            complexity=LLMComplexity.COMPLEX,
            prompt=prompt,
            system_prompt=INTENT_PARSER_SYSTEM_PROMPT,
            prompt_key="prospecting.intent_parser.user",
            system_prompt_key="prospecting.intent_parser.system",
            schema=IntentParseResult,
            variables={
                "objective": objective,
                "target": target,
                "qualification": qualification,
                "clarification_history": clarification_history
            },
            metadata={"domain": "prospecting", "feature": "intent_parser"}
        )
        llm_res = llm_service.execute(req)
        if llm_res.is_success() and isinstance(llm_res.output, IntentParseResult):
            return llm_res.output
            
        # Fallback handling for response format dictionary compatibility
        res = {
            "type": "text" if llm_res.is_success() else "error",
            "text": llm_res.raw_text or llm_res.error_message or "",
            "provider": llm_res.provider,
            "model": llm_res.model
        }
        parsed_result = self._handle_llm_response(res, prompt)
        return parsed_result

    def _handle_llm_response(self, response_dict: Dict[str, Any], original_prompt: str, is_retry: bool = False) -> IntentParseResult:
        if response_dict.get("type") == "error":
            logger.error(f"LLM Router returned generation error: {response_dict.get('text')}")
            return self._build_invalid_fallback_result(f"Router error: {response_dict.get('text')}")

        output_text = response_dict.get("text", "").strip()
        provider = response_dict.get("provider", "unknown")
        model = response_dict.get("model", "unknown")

        try:
            parsed_json = self._extract_json(output_text)
            # Ensure pydantic validation passes
            result_obj = IntentParseResult.model_validate(parsed_json)
            result_obj.parser_model = model
            result_obj.parser_provider = provider
            
            # Post-check validation rules programmatically
            validation_errors = ProspectingSpecificationValidator.validate_specification(result_obj.specification)
            if validation_errors:
                result_obj.status = "INVALID"
                result_obj.missing_information.extend(validation_errors)
            else:
                # Post-check clarification rules programmatically
                missing_critical = ProspectingIntakeClarifier.get_missing_fields(result_obj.specification)
                if missing_critical:
                    result_obj.status = "NEEDS_CLARIFICATION"
                    result_obj.missing_information.extend(missing_critical)
                    # Guarantee we have at least one question
                    if not result_obj.clarification_questions:
                        result_obj.clarification_questions.append(
                            f"Please provide more details regarding your missing target criteria: {', '.join(missing_critical)}."
                        )
            
            return result_obj

        except Exception as e:
            logger.warning(f"Failed parsing or validating LLM response. Error: {str(e)}. Raw output: {output_text[:500]}")
            if not is_retry:
                # Trigger correction retry prompt
                correction_prompt = (
                    f"{original_prompt}\n\n"
                    "### ERROR CORRECTION ACTION REQUIRED:\n"
                    f"Your previous output failed validation with the following error: {str(e)}.\n"
                    "Please output a strictly valid JSON object conforming to the required schema definition."
                )
                retry_res = self.router.generate(
                    prompt=correction_prompt,
                    system_prompt=INTENT_PARSER_SYSTEM_PROMPT,
                    system_prompt_key="prospecting.intent_parser.system"
                )
                return self._handle_llm_response(retry_res, original_prompt, is_retry=True)
            
            return self._build_invalid_fallback_result(f"JSON validation failure: {str(e)}")

    def _extract_json(self, text: str) -> Dict[str, Any]:
        """Extracts JSON dictionary substring if text is wrapped in markdown codeblocks."""
        text_clean = text.strip()
        if text_clean.startswith("```json"):
            text_clean = text_clean[7:]
        elif text_clean.startswith("```"):
            text_clean = text_clean[3:]
        if text_clean.endswith("```"):
            text_clean = text_clean[:-3]
        
        # Locate first '{' and last '}'
        start = text_clean.find("{")
        end = text_clean.rfind("}")
        if start != -1 and end != -1:
            text_clean = text_clean[start:end+1]
            
        return json.loads(text_clean.strip())

    def _build_invalid_fallback_result(self, reason: str) -> IntentParseResult:
        spec = ProspectingSpecification()
        spec.objective.value = ""
        spec.objective.provenance = Provenance.SYSTEM_DEFAULT
        return IntentParseResult(
            status="INVALID",
            specification=spec,
            missing_information=[reason],
            clarification_questions=["Please rewrite your request with more detail."],
            assumptions=["Model failed to interpret inputs safely."],
            confidence=0.0
        )
