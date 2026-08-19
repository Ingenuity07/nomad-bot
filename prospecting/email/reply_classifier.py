import json
import logging
from llm.router import IntelligentRouter
from prospecting.models import InboundReply, EmailUnsubscribe

logger = logging.getLogger(__name__)
router = IntelligentRouter()

class ReplyClassifier:
    @staticmethod
    def classify_reply(reply: InboundReply) -> dict:
        """
        Classifies incoming replies using LLM router, updates DB fields, 
        and adds to unsubscribe suppressions if unsubscribe intent is detected.
        """
        text = reply.reply_text
        
        prompt = (
            f"Analyze this incoming sales prospect email reply and classify its sentiment class:\n"
            f"Reply Text: '{text}'\n\n"
            f"Choose the single most accurate category from this list:\n"
            f"INTERESTED, QUESTION, NOT_NOW, NOT_INTERESTED, WRONG_PERSON, UNSUBSCRIBE, OUT_OF_OFFICE, UNKNOWN.\n"
            f"Calculate classification confidence (float between 0.0 and 1.0)."
        )

        schema = (
            "{"
            '  "classification": "string (INTERESTED/QUESTION/NOT_NOW/NOT_INTERESTED/WRONG_PERSON/UNSUBSCRIBE/OUT_OF_OFFICE/UNKNOWN)",'
            '  "confidence": 0.95,'
            '  "reason": "string"'
            "}"
        )

        system_prompt = "You are a senior reply intelligence sentiment classifier. Return ONLY structured raw JSON."
        full_prompt = f"{prompt}\n\nSchema:\n{schema}\n\nReturn ONLY raw JSON."
        
        try:
            result = router.generate(
                prompt=full_prompt,
                system_prompt=system_prompt,
                prompt_key="prospecting.reply_classifier.user",
                system_prompt_key="prospecting.reply_classifier.system",
                template_variables={"reply_text": text}
            )
            res_text = result.get("text", "").strip()

            # Clean markdown frames
            if res_text.startswith("```json"):
                res_text = res_text[7:]
            elif res_text.startswith("```"):
                res_text = res_text[3:]
            if res_text.endswith("```"):
                res_text = res_text[:-3]
            res_text = res_text.strip()

            data = json.loads(res_text)
            classification = data.get("classification", "UNKNOWN").upper()
            
            allowed = ['INTERESTED', 'QUESTION', 'NOT_NOW', 'NOT_INTERESTED', 'WRONG_PERSON', 'UNSUBSCRIBE', 'OUT_OF_OFFICE', 'UNKNOWN']
            if classification not in allowed:
                classification = "UNKNOWN"

            confidence = float(data.get("confidence", 1.0))
            
            reply.classification = classification
            reply.confidence = confidence
            
            # Set review flag if LLM confidence is low
            if confidence < 0.70:
                reply.requires_review = True
                
            reply.save()

            # Compliance trigger: auto-suppress unsubscribe requests
            if classification == "UNSUBSCRIBE":
                recipient = reply.email_message.recipient_email
                EmailUnsubscribe.objects.get_or_create(email=recipient)
                logger.info(f"Compliance auto-unsubscribe applied for: {recipient}")

            return {
                "classification": classification,
                "confidence": confidence,
                "requires_review": reply.requires_review
            }

        except Exception as e:
            logger.error(f"Error classifying reply: {e}")
            reply.classification = "UNKNOWN"
            reply.save()
            return {"classification": "UNKNOWN", "confidence": 0.0, "requires_review": True}
