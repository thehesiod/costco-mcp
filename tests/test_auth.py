"""Unit tests for auth token refresh and policy fallback."""

from unittest.mock import MagicMock, patch

import pytest

from costco_mcp_server.auth import POLICY_NAME, _refresh_tokens


def test_default_policy_is_201():
    assert POLICY_NAME == "b2c_1a_sso_wcs_signup_signin_201"


def test_refresh_tokens_retries_on_expected_policy_html():
    first = MagicMock()
    first.status_code = 400
    first.text = (
        "<html>AADB2C90079: The request does not contain the expected policy. "
        "Expected Value : b2c_1a_sso_wcs_signup_signin_209</html>"
    )

    second = MagicMock()
    second.status_code = 200
    second.json.return_value = {"id_token": "id", "refresh_token": "rt2"}
    second.raise_for_status = MagicMock()

    with patch(
        "costco_mcp_server.auth.curl_requests.post",
        side_effect=[first, second],
    ) as mock_post:
        res = _refresh_tokens("rt1")

    assert res["id_token"] == "id"
    assert res["refresh_token"] == "rt2"
    assert res["_policy_name"] == "b2c_1a_sso_wcs_signup_signin_209"
    assert mock_post.call_count == 2
    first_url = mock_post.call_args_list[0].args[0]
    retry_url = mock_post.call_args_list[1].args[0]
    assert "signup_signin_201" in first_url
    assert "signup_signin_209" in retry_url


def test_refresh_tokens_400_without_expected_value_raises():
    resp = MagicMock()
    resp.status_code = 400
    resp.text = "<html>Akamai interstitial</html>"
    resp.raise_for_status.side_effect = Exception("HTTP 400")

    with patch("costco_mcp_server.auth.curl_requests.post", return_value=resp):
        with pytest.raises(Exception, match="HTTP 400"):
            _refresh_tokens("rt1")
