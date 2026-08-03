import re
import logging
import requests
from bs4 import BeautifulSoup
from memory.models import LeadCompany, LeadContact

logger = logging.getLogger(__name__)

# Standard emails, phone numbers, and LinkedIn regexes
EMAIL_REGEX = re.compile(r'[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}')
PHONE_REGEX = re.compile(r'(?:\+?44\s?(?:\d{3,4}\s?\d{3,4}|\(\d{3,4}\)\s?\d{3,4})|\b0\d{2,4}\s?\d{5,8}\b)')
LINKEDIN_REGEX = re.compile(r'https?://(?:www\.)?linkedin\.com/(?:in|company)/[a-zA-Z0-9_-]+')

class ContactExtractor:
    """Crawls business sites to extract emails, phone numbers, and social links."""

    @staticmethod
    def extract_contacts(company: LeadCompany) -> list:
        if not company.website:
            logger.info(f"No website resolved for company {company.name}. Skipping contact extraction.")
            return []

        discovered = []
        visited = set()
        to_visit = [company.website]
        headers = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}

        # Keep track of unique findings to prevent DB duplicates
        found_emails = set()
        found_phones = set()
        found_linkedins = set()

        # Crawl homepage and up to 4 key subpages
        pages_limit = 5
        pages_visited = 0

        while to_visit and pages_visited < pages_limit:
            current_url = to_visit.pop(0)
            if current_url in visited:
                continue
            
            visited.add(current_url)
            pages_visited += 1

            try:
                logger.info(f"Crawling page: {current_url}")
                res = requests.get(current_url, headers=headers, timeout=8)
                if res.status_code != 200:
                    continue

                html = res.text
                soup = BeautifulSoup(html, "html.parser")
                text = soup.get_text()

                # 1. Extract Emails
                emails = EMAIL_REGEX.findall(text)
                for email in emails:
                    email_clean = email.lower().strip()
                    # Filter out common false positives/image extensions
                    if email_clean not in found_emails and not any(email_clean.endswith(ext) for ext in [".png", ".jpg", ".gif", ".webp", ".svg"]):
                        found_emails.add(email_clean)
                        contact = LeadContact.objects.create(
                            company=company,
                            email=email_clean,
                            source=current_url
                        )
                        discovered.append(contact)

                # 2. Extract Phone Numbers
                phones = PHONE_REGEX.findall(text)
                for phone in phones:
                    phone_clean = phone.strip()
                    if phone_clean not in found_phones and len(phone_clean) > 8:
                        found_phones.add(phone_clean)
                        # Update company phone if not set, or save as contact
                        if not company.phone:
                            company.phone = phone_clean
                            company.save()

                # 3. Extract LinkedIn
                for link in soup.find_all("a", href=True):
                    href = link["href"]
                    match = LINKEDIN_REGEX.search(href)
                    if match:
                        li_url = match.group(0)
                        if li_url not in found_linkedins:
                            found_linkedins.add(li_url)
                            # Create a contact record for LinkedIn
                            contact = LeadContact.objects.create(
                                company=company,
                                email="linkedin@placeholder.com",
                                linkedin=li_url,
                                role="Company Page",
                                source=current_url
                            )
                            discovered.append(contact)

                # 4. Find internal links to visit next (only on the homepage/first turn)
                if pages_visited == 1:
                    for link in soup.find_all("a", href=True):
                        href = link["href"]
                        # Resolve relative links
                        if href.startswith("/"):
                            href = company.website.rstrip("/") + href
                        
                        href_lower = href.lower()
                        # Pick high-value contact pages
                        if any(k in href_lower for k in ["contact", "about", "support", "hello", "reach", "info", "terms"]):
                            if href not in visited and href.startswith(company.website):
                                to_visit.append(href)

            except Exception as e:
                logger.error(f"Error crawling {current_url} for contacts: {e}")

        return discovered
