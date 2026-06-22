from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger
from odoo.addons.ocr_techdata.services import jwt_auth

_JWT_LOGGER = "odoo.addons.ocr_techdata.services.jwt_auth"


class TestJwtAuth(TransactionCase):
    def setUp(self):
        super().setUp()
        jwt_auth.clear_token_cache()

    def _make_env_mock(self, client_id="odoo-prod", client_secret="secret", refresh_token=""):
        icp = MagicMock()
        params = {
            "ocr_techdata.client_id": client_id,
            "ocr_techdata.client_secret": client_secret,
            "ocr_techdata.refresh_token": refresh_token,
        }
        icp.get_param.side_effect = lambda key, default="": params.get(key, default)
        env = MagicMock()
        env.__getitem__.return_value = MagicMock(sudo=lambda: icp)
        return env, icp

    def test_obtain_tokens_on_first_call(self):
        env, icp = self._make_env_mock()
        token_response = {
            "access_token": "access.token.value",
            "refresh_token": "refresh.token.value",
            "expires_in": 900,
        }
        with patch("odoo.addons.ocr_techdata.services.jwt_auth.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: token_response,
                raise_for_status=lambda: None,
            )
            result = jwt_auth.get_access_token(env)

        self.assertEqual(result, "access.token.value")
        icp.set_param.assert_called_with("ocr_techdata.refresh_token", "refresh.token.value")

    def test_cached_token_not_refreshed(self):
        env, _ = self._make_env_mock()
        import time
        jwt_auth._ACCESS_TOKEN = "cached.token"
        jwt_auth._ACCESS_EXPIRES_AT = time.time() + 900

        with patch("requests.Session.post") as mock_post:
            result = jwt_auth.get_access_token(env)
            mock_post.assert_not_called()

        self.assertEqual(result, "cached.token")

    def test_refresh_used_when_token_nearly_expired(self):
        env, icp = self._make_env_mock(refresh_token="existing.refresh.token")
        import time
        jwt_auth._ACCESS_TOKEN = "old.token"
        jwt_auth._ACCESS_EXPIRES_AT = time.time() + 30  # within refresh-ahead window

        refresh_response = {"access_token": "new.access.token", "expires_in": 900}
        with patch("odoo.addons.ocr_techdata.services.jwt_auth.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: refresh_response,
                raise_for_status=lambda: None,
            )
            result = jwt_auth.get_access_token(env)

        self.assertEqual(result, "new.access.token")

    def test_refresh_persists_rotated_token(self):
        """mars rotates the refresh token on /auth/refresh — the rotated token in the
        response must be persisted to ICP, otherwise the next refresh 401s."""
        env, icp = self._make_env_mock(refresh_token="old.refresh.token")
        import time
        jwt_auth._ACCESS_TOKEN = "old.token"
        jwt_auth._ACCESS_EXPIRES_AT = time.time() + 30  # within refresh-ahead window

        refresh_response = {
            "access_token": "new.access.token",
            "refresh_token": "rotated.refresh.token",
            "expires_in": 900,
        }
        with patch("odoo.addons.ocr_techdata.services.jwt_auth.requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                json=lambda: refresh_response,
                raise_for_status=lambda: None,
            )
            result = jwt_auth.get_access_token(env)

        self.assertEqual(result, "new.access.token")
        icp.set_param.assert_called_with("ocr_techdata.refresh_token", "rotated.refresh.token")

    @mute_logger(_JWT_LOGGER)
    def test_refresh_401_clears_stale_token(self):
        """A 401 on refresh must clear the stale token from ICP and fall back to re-auth."""
        env, icp = self._make_env_mock(refresh_token="stale.refresh.token")
        import time
        jwt_auth._ACCESS_TOKEN = "old.token"
        jwt_auth._ACCESS_EXPIRES_AT = time.time() + 30

        reauth_response = {
            "access_token": "reauth.access.token",
            "refresh_token": "fresh.refresh.token",
            "expires_in": 900,
        }

        def post_side_effect(url, **kwargs):
            if url.endswith("/auth/refresh"):
                return MagicMock(status_code=401, json=lambda: {}, raise_for_status=lambda: None)
            return MagicMock(status_code=200, json=lambda: reauth_response, raise_for_status=lambda: None)

        with patch("odoo.addons.ocr_techdata.services.jwt_auth.requests.post", side_effect=post_side_effect):
            result = jwt_auth.get_access_token(env)

        self.assertEqual(result, "reauth.access.token")
        # stale token cleared, then fresh token from re-auth persisted
        icp.set_param.assert_any_call("ocr_techdata.refresh_token", "")
        icp.set_param.assert_any_call("ocr_techdata.refresh_token", "fresh.refresh.token")

    @mute_logger(_JWT_LOGGER)
    def test_missing_credentials_returns_none(self):
        env, _ = self._make_env_mock(client_id="", client_secret="")
        result = jwt_auth.get_access_token(env)
        self.assertIsNone(result)

    @mute_logger(_JWT_LOGGER)
    def test_connection_error_returns_none(self):
        import requests as req
        env, _ = self._make_env_mock()
        with patch(
            "odoo.addons.ocr_techdata.services.jwt_auth.requests.post",
            side_effect=req.exceptions.ConnectionError,
        ):
            result = jwt_auth.get_access_token(env)
        self.assertIsNone(result)

    def test_clear_token_cache(self):
        import time
        jwt_auth._ACCESS_TOKEN = "some.token"
        jwt_auth._ACCESS_EXPIRES_AT = time.time() + 900
        jwt_auth.clear_token_cache()
        self.assertIsNone(jwt_auth._ACCESS_TOKEN)
        self.assertEqual(jwt_auth._ACCESS_EXPIRES_AT, 0.0)
