"""pathiel — autonomous quant trading agent for Hyperliquid."""

# FIRST, before any module below reads the environment: the project was
# renamed from pathia, and 65 PATHIA_* variables are still set in .env.local,
# in `fly secrets` and on Vercel. compat mirrors both spellings so none of
# them quietly stops being read. See pathiel/compat.py.
from pathiel import compat as _compat  # noqa: F401

import os
import ssl
import certifi

__version__ = "0.3.0"

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
