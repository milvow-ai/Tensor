from farm.connect.drivers.base import Identity
from farm.connect.service import _identity_json


def test_displayed_account_identity_comes_from_the_driver_not_its_label() -> None:
    actual = Identity("provider-id", "person@example.com", display_name="Person")
    saved = _identity_json(actual, "hash")
    assert saved["email"] == "person@example.com" and saved["display_name"] == "Person"
    assert saved["source"] == "provider_login" and saved["email_masked"] != saved["email"]
    assert "provider_account_id" not in saved
