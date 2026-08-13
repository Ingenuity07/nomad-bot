import os
import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ProspectingConfig(AppConfig):
    name = 'prospecting'
    
    # Global state tracker for discovery data-providers availability
    providers_status = {
        'google_places': {'available': False, 'reason': 'uninitialized'},
        'apify': {'available': False, 'reason': 'uninitialized'},
        'search': {'available': True, 'reason': 'active'}
    }

    def ready(self):
        # Import providers to trigger registration registry side-effects
        from prospecting.discovery.providers import google_places, apify, search

        # Validate Google Maps API Key
        google_key = os.environ.get('GOOGLE_MAPS_API_KEY', '').strip()
        if google_key:
            self.providers_status['google_places'] = {'available': True, 'reason': 'active'}
        else:
            self.providers_status['google_places'] = {'available': False, 'reason': 'missing_credentials'}
            logger.warning("GOOGLE_MAPS_API_KEY is not set. Google Places provider disabled.")

        # Validate Apify Token
        apify_token = os.environ.get('APIFY_API_TOKEN', '').strip()
        if apify_token:
            self.providers_status['apify'] = {'available': True, 'reason': 'active'}
        else:
            self.providers_status['apify'] = {'available': False, 'reason': 'missing_credentials'}
            logger.warning("APIFY_API_TOKEN is not set. Apify provider disabled.")

