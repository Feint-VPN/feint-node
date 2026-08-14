"""Unit checks for the legacy initializer wrapper."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "init-node.sh"


def test_legacy_initializer_validates_the_old_argument_shape():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "if [[ $# -ne 3 ]]" in content
    assert "Invalid domain" in content
    assert "Invalid email" in content
    assert "Invalid IPv4 address" in content


def test_legacy_initializer_does_not_restore_the_api_driven_flow():
    content = SCRIPT.read_text(encoding="utf-8")

    assert "API-driven initializer is retired" in content
    assert "/initialize" not in content
    assert "local.docker-compose.yaml" not in content
