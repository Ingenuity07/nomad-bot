import os
import logging
from django.apps import AppConfig

logger = logging.getLogger(__name__)


class ProspectingConfig(AppConfig):
    name = 'prospecting'
    
    # Global state tracker for discovery data-providers availability
    providers_status = {
        'google_places': {'available': False, 'reason': 'uninitialized'},
        'apollo': {'available': False, 'reason': 'uninitialized'},
        'apify': {'available': False, 'reason': 'uninitialized'},
        'search': {'available': True, 'reason': 'active'}
    }

    def ready(self):
        # Import providers to trigger registration registry side-effects
        from prospecting.discovery.providers import apify, apollo, google_places, search
        from prospecting.discovery.providers.config import provider_status

        self.providers_status['google_places'] = provider_status(
            'GOOGLE_PLACES_ENABLED', 'GOOGLE_PLACES_API_KEY', 'GOOGLE_MAPS_API_KEY'
        )
        self.providers_status['apollo'] = provider_status('APOLLO_ENABLED', 'APOLLO_API_KEY')

        for provider_name in ('google_places', 'apollo'):
            status = self.providers_status[provider_name]
            if not status['available']:
                logger.info(
                    "%s provider unavailable: %s",
                    provider_name,
                    status['reason'],
                )

        # Validate Apify Token
        apify_token = os.environ.get('APIFY_API_TOKEN', '').strip()
        if apify_token:
            self.providers_status['apify'] = {'available': True, 'reason': 'active'}
        else:
            self.providers_status['apify'] = {'available': False, 'reason': 'missing_credentials'}
            logger.warning("APIFY_API_TOKEN is not set. Apify provider disabled.")

