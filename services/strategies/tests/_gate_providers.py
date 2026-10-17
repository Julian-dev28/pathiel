"""Gate-safe stand-ins for the real intel and thesis providers.

Once services/intel and services/thesis exist, the pipeline's default provider
paths resolve to code that hits the network and the billable LLM. Gate tests
must do neither, so conftest points the pipeline here by default. A test that
needs specific provider behavior still monkeypatches `_resolve_provider` or the
path constants itself, which overrides this.
"""
from datetime import datetime, timezone

from contracts.strategies.models import IntelPacket


def build_packet(spec, now=None):
    return IntelPacket(desk_id=spec.id, as_of=now or datetime.now(timezone.utc),
                       sources_failed=["gate-test stub: no network"])


def write_thesis(spec, analytics, intel, prev):
    raise RuntimeError("thesis provider disabled in gate tests (no LLM calls)")
