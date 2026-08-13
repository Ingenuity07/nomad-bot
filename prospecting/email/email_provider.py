import logging
from django.utils import timezone
from datetime import timedelta
from abc import ABC, abstractmethod
from prospecting.models import EmailMessage, EmailUnsubscribe, EmailBounce

logger = logging.getLogger(__name__)

class EmailProvider(ABC):
    @abstractmethod
    def send(self, message: EmailMessage) -> bool:
        pass

    @abstractmethod
    def get_message(self, message_id: str) -> dict:
        pass

    @abstractmethod
    def sync_replies(self) -> list:
        pass

    @abstractmethod
    def health_check(self) -> bool:
        pass


class MockEmailProvider(EmailProvider):
    def send(self, message: EmailMessage) -> bool:
        """
        Sends the email message after verifying safety suppression, approval flags, and daily rate limits.
        """
        email = message.recipient_email

        # 1. Human Approval safety check
        if not message.is_approved:
            logger.warning(f"Email send blocked: Message to {email} is pending manual approval.")
            message.status = 'PENDING_APPROVAL'
            message.save()
            return False

        # 2. Unsubscribe suppression check
        if EmailUnsubscribe.objects.filter(email=email).exists():
            logger.warning(f"Email send blocked: Recipient {email} is unsubscribed.")
            message.status = 'CANCELLED'
            message.save()
            return False

        # 3. Bounce suppression check
        if EmailBounce.objects.filter(email=email).exists():
            logger.warning(f"Email send blocked: Recipient {email} is on the bounce list.")
            message.status = 'FAILED'
            message.save()
            return False

        # 4. Daily limits safety check
        today_start = timezone.now().replace(hour=0, minute=0, second=0, microsecond=0)
        sent_today = EmailMessage.objects.filter(
            status='SENT',
            sent_at__gte=today_start
        ).count()

        if sent_today >= 100:
            logger.warning(f"Email send blocked: Daily sending rate limit of 100 emails reached.")
            return False

        # Simulate successful transmission
        logger.info(f"Simulating email sent successfully to {email}: {message.subject}")
        message.status = 'SENT'
        message.sent_at = timezone.now()
        message.save()
        return True

    def get_message(self, message_id: str) -> dict:
        return {"id": message_id, "status": "sent"}

    def sync_replies(self) -> list:
        return []

    def health_check(self) -> bool:
        return True
