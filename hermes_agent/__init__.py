"""hermes-agent — autonomous quant trading agent for Hyperliquid."""

import os
import ssl
import certifi

__version__ = "0.2.0"

# Fix SSL cert verification on macOS (system Python lacks cacert.pem)
if not os.environ.get("NO_SSL_FIX"):
    os.environ["REQUESTS_CA_BUNDLE"] = certifi.where()
    os.environ["SSL_CERT_FILE"] = certifi.where()
    if hasattr(ssl._ssl, "_get_default_verify_paths"):
        # Override SSL context defaults
        _orig_create_default_context = ssl.create_default_context
        def _patched_create_default_context(*args, **kwargs):
            ctx = _orig_create_default_context(*args, **kwargs)
            ctx.load_default_certs()
            return ctx
        ssl.create_default_context = _patched_create_default_context
