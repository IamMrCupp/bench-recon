"""compose.yaml publishes bench-recon on loopback, on the suite's port.

bench-recon holds client engagement records, so the published port must stay on
127.0.0.1. 8403 is its host port in the bench suite (intake 8401, parts 8402);
the container keeps 8000, so the image and the Kubernetes manifests don't move.
"""

import re
from pathlib import Path

COMPOSE = Path(__file__).resolve().parent.parent / "compose.yaml"


def _published_ports() -> list[str]:
    lines = COMPOSE.read_text().splitlines()
    return [
        m.group(1)
        for line in lines
        if (m := re.match(r'\s*-\s*"([^"]*:\d+)"\s*$', line)) and not line.lstrip().startswith("#")
    ]


def test_compose_publishes_loopback_8403_to_the_containers_8000() -> None:
    assert _published_ports() == ["127.0.0.1:8403:8000"]


def test_compose_never_binds_a_lan_address() -> None:
    for mapping in _published_ports():
        assert mapping.startswith("127.0.0.1:"), mapping
