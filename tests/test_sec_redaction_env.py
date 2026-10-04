"""Security tests for environment secret redaction (Requirement 2).

register_env_secrets() registers all secret-looking environment values and DB passwords,
ensuring redact() masks them inside arbitrary strings and error messages.
"""

import os

from farm.secrets import redact, register_env_secrets
from farm.settings import load_env


def test_register_env_secrets_redaction() -> None:
    """register_env_secrets should find secret-looking variables and DB passwords and redact them."""
    secret_pw = "my-secret-pass-word"
    secret_api_key = "my-provider-secret-key-999"
    secret_token = "my-custom-auth-token-888"
    secret_vk = "my-bifrost-vk-777"
    db_url = f"postgresql://app_user:{secret_pw}" + "@" + "127.0.0.1:5432/production_db"

    orig_env = dict(os.environ)
    try:
        os.environ["FARM_DB_URL"] = db_url
        os.environ["PROVIDER_API_KEY"] = secret_api_key
        os.environ["ACCESS_TOKEN"] = secret_token
        os.environ["BIFROST_VK"] = secret_vk

        # Trigger registration
        register_env_secrets()

        # Test redaction over arbitrary text containing these secrets
        sample_error = (
            f"Failed to connect to {db_url} using password {secret_pw}. "
            f"Key={secret_api_key}, token was {secret_token}, vk was {secret_vk}."
        )

        redacted = redact(sample_error)

        assert secret_pw not in redacted
        assert secret_api_key not in redacted
        assert secret_token not in redacted
        assert secret_vk not in redacted
        assert "***" in redacted
    finally:
        os.environ.clear()
        os.environ.update(orig_env)


def test_load_env_calls_register_env_secrets() -> None:
    """load_env() must call register_env_secrets() at the end."""
    secret_val = "load-env-secret-val-4321"
    orig_env = dict(os.environ)
    try:
        os.environ["EXTERNAL_SECRET_KEY"] = secret_val
        load_env()

        text = f"An unhandled error occurred: {secret_val}"
        cleaned = redact(text)
        assert secret_val not in cleaned
        assert "***" in cleaned
    finally:
        os.environ.clear()
        os.environ.update(orig_env)


def test_url_forms_and_percent_encoded_password_redaction() -> None:
    """register_env_secrets must cover postgresql+psycopg, redis, https, and percent-encoded passwords."""
    raw_encoded_pw = "p%40ss"  # unquotes to "p@ss"
    unquoted_pw = "p@ss"

    scheme_pg = "postgresql+psycopg"
    pg_url = f"{scheme_pg}://farm_user:{raw_encoded_pw}@localhost:5432/mydb"
    redis_pw = "redispw"
    redis_url = f"redis://:{redis_pw}@127.0.0.1:6379/0"
    https_pw = "httppw"
    https_url = f"https://myuser:{https_pw}@api.example.com/v1"

    vk_val = "vk_four_chars"

    orig_env = dict(os.environ)
    try:
        os.environ["FARM_DB_URL"] = pg_url
        os.environ["CACHE_URL"] = redis_url
        os.environ["PROXY_ENDPOINT"] = https_url
        os.environ["APP_KEY"] = "key123"
        os.environ["MY_SECRET"] = "sec456"
        os.environ["USER_TOKEN"] = "tok789"
        os.environ["SYS_PASSWORD"] = "pass321"
        os.environ["TARGET_VK"] = vk_val

        register_env_secrets()

        # Both raw percent-encoded and unquoted passwords must be redacted
        text_with_raw = f"Connection failed for {pg_url} with raw pw {raw_encoded_pw}"
        redacted_raw = redact(text_with_raw)
        assert raw_encoded_pw not in redacted_raw
        assert unquoted_pw not in redacted_raw

        text_with_unquoted = f"Auth failed with unquoted password: {unquoted_pw}"
        redacted_unquoted = redact(text_with_unquoted)
        assert unquoted_pw not in redacted_unquoted
        assert raw_encoded_pw not in redacted_unquoted

        # Redis password must be redacted
        text_redis = f"Redis error at {redis_url} using {redis_pw}"
        assert redis_pw not in redact(text_redis)

        # HTTPS password must be redacted
        text_https = f"HTTPS request failed: {https_url} auth={https_pw}"
        assert https_pw not in redact(text_https)

        # Secret named vars >= 4 chars must be redacted
        for secret_item in ("key123", "sec456", "tok789", "pass321", vk_val):
            assert secret_item not in redact(f"Debug item: {secret_item}")
    finally:
        os.environ.clear()
        os.environ.update(orig_env)

