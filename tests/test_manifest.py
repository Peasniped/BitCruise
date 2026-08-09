"""Validate the integration manifest and translation files.

These are pure file checks with no Home Assistant dependency, so they run on any
platform. They guard the metadata that Home Assistant and HACS rely on but that
neither validates until the integration is already installed or published.
"""

import json
from pathlib import Path

import pytest

INTEGRATION_DIR = Path(__file__).parent.parent / "custom_components" / "bitcruise"
MANIFEST_PATH = INTEGRATION_DIR / "manifest.json"
STRINGS_PATH = INTEGRATION_DIR / "strings.json"
EN_TRANSLATION_PATH = INTEGRATION_DIR / "translations" / "en.json"

# https://developers.home-assistant.io/docs/creating_integration_manifest/
VALID_INTEGRATION_TYPES = {
    "device",
    "entity",
    "hardware",
    "helper",
    "hub",
    "service",
    "system",
    "virtual",
}
VALID_IOT_CLASSES = {
    "assumed_state",
    "cloud_polling",
    "cloud_push",
    "local_polling",
    "local_push",
    "calculated",
}

# Required by HACS for a published integration.
HACS_REQUIRED_KEYS = {
    "domain",
    "documentation",
    "issue_tracker",
    "codeowners",
    "name",
    "version",
}


@pytest.fixture
def manifest() -> dict:
    """Return the parsed integration manifest."""
    return json.loads(MANIFEST_PATH.read_text(encoding="utf-8"))


def test_manifest_has_hacs_required_keys(manifest: dict) -> None:
    """HACS refuses to publish an integration missing any of these."""
    assert manifest.keys() >= HACS_REQUIRED_KEYS


def test_domain_matches_directory_name(manifest: dict) -> None:
    """The domain must equal the folder name, or the integration will not load."""
    assert manifest["domain"] == INTEGRATION_DIR.name == "bitcruise"


def test_integration_type_is_valid(manifest: dict) -> None:
    """The declared integration type must be one Home Assistant recognises."""
    assert manifest["integration_type"] in VALID_INTEGRATION_TYPES


def test_integration_type_is_not_helper(manifest: dict) -> None:
    """Regression: integration_type must not be 'helper'.

    'helper' files the integration under the Helpers tab rather than Integrations,
    and the Helpers UI assumes every helper is editable. Clicking the entry opens an
    options flow, so an integration without one fails with
    'Config flow could not be loaded: Invalid handler specified'.
    See home-assistant/frontend#15044.
    """
    assert manifest["integration_type"] != "helper"


def test_iot_class_is_valid(manifest: dict) -> None:
    """The declared IoT class must be one Home Assistant recognises."""
    assert manifest["iot_class"] in VALID_IOT_CLASSES


def test_version_is_present_and_sane(manifest: dict) -> None:
    """Custom integrations must carry a version; HACS parses it as a release."""
    version = manifest["version"]
    parts = version.split(".")
    assert len(parts) == 3, f"expected semver, got {version!r}"
    assert all(part.isdigit() for part in parts), f"non-numeric part in {version!r}"


def test_no_runtime_requirements(manifest: dict) -> None:
    """V1 dependency policy: no third-party runtime dependencies (CLAUDE.md)."""
    assert manifest["requirements"] == []


def test_config_flow_is_declared(manifest: dict) -> None:
    """The integration is set up through the UI, so config_flow must be true."""
    assert manifest["config_flow"] is True


def test_english_translation_matches_strings() -> None:
    """translations/en.json must stay in sync with strings.json.

    Home Assistant serves strings.json to developers and translations/en.json to
    users. They drift silently, and the drift is only visible in the UI.
    """
    strings = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    english = json.loads(EN_TRANSLATION_PATH.read_text(encoding="utf-8"))
    assert strings == english


def test_config_flow_steps_are_translated() -> None:
    """Every config flow step referenced in strings.json needs a title."""
    strings = json.loads(STRINGS_PATH.read_text(encoding="utf-8"))
    for step_id, step in strings["config"]["step"].items():
        assert step.get("title"), f"step {step_id!r} has no title"
