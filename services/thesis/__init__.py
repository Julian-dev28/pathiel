"""services/thesis - the one latent step in the desk pipeline.

    from services.thesis import write_thesis
    from services.thesis.render import render_markdown
"""
from __future__ import annotations

from services.thesis.api import write_thesis

__all__ = ["write_thesis"]
