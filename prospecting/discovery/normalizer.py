import re
from urllib.parse import urlparse

class Normalizer:
    @staticmethod
    def normalize_name(name: str) -> str:
        """Lowercase, strip whitespace, and remove common corporate suffixes for matching."""
        if not name:
            return ""
        name = name.lower().strip()
        # Remove common legal entity suffixes
        suffixes = [
            r"\bco\b", r"\bltd\b", r"\blimited\b", r"\binc\b", r"\bincorporated\b",
            r"\bplc\b", r"\bcorp\b", r"\bcorporation\b", r"\bllc\b", r"\bllp\b"
        ]
        pattern = "|".join(suffixes)
        # Strip trailing punctuation and spaces
        cleaned = re.sub(pattern, "", name)
        cleaned = re.sub(r"[^\w\s]", "", cleaned)
        return " ".join(cleaned.split())

    @staticmethod
    def normalize_domain(url: str) -> str:
        """Extract and normalize host domain from any URL."""
        if not url:
            return ""
        url = url.strip().lower()
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        try:
            parsed = urlparse(url)
            domain = parsed.netloc
            if domain.startswith("www."):
                domain = domain[4:]
            return domain
        except Exception:
            return ""

    @staticmethod
    def normalize_phone(phone: str) -> str:
        """Strip formatting, keep digits and optional leading plus sign."""
        if not phone:
            return ""
        phone = phone.strip()
        has_plus = phone.startswith("+")
        digits = "".join(filter(str.isdigit, phone))
        return f"+{digits}" if has_plus else digits
