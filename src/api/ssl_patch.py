"""SSL patch for hyundai_kia_connect_api to handle certificate issues"""

import logging
import ssl

import urllib3

logger = logging.getLogger(__name__)
from requests.adapters import HTTPAdapter

# Disable SSL warnings
urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)


class SSLAdapter(HTTPAdapter):
    """An HTTPS Transport Adapter that uses a custom SSL context."""

    def init_poolmanager(self, *args, **kwargs):
        ctx = ssl.create_default_context()
        ctx.check_hostname = False
        ctx.verify_mode = ssl.CERT_NONE
        kwargs["ssl_context"] = ctx
        return super().init_poolmanager(*args, **kwargs)


def patch_hyundai_ssl():
    """Patch the hyundai_kia_connect_api to disable SSL verification"""
    try:
        # Import the API modules
        from hyundai_kia_connect_api import ApiImpl

        # Store the original session creation
        original_init = ApiImpl.__init__

        def patched_init(self, *args, **kwargs):
            # Call original init
            original_init(self, *args, **kwargs)

            # Replace the session's adapter
            if hasattr(self, "sessions"):
                adapter = SSLAdapter()
                self.sessions.mount("https://", adapter)
                self.sessions.verify = False
                logger.info("SSL verification disabled for Hyundai API")

        # Apply the patch
        ApiImpl.__init__ = patched_init
        return True

    except Exception as e:
        logger.error("Failed to patch SSL: %s", e)
        return False
