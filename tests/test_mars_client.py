from unittest.mock import MagicMock, patch

import requests

from odoo.tests.common import TransactionCase
from odoo.addons.ocr_techdata.services import mars_client, jwt_auth


class TestMarsClient(TransactionCase):
    def setUp(self):
        super().setUp()
        jwt_auth.clear_token_cache()

    def _make_env_mock(self):
        icp = MagicMock()
        icp.get_param.return_value = ""
        env = MagicMock()
        env.__getitem__.return_value = MagicMock(sudo=lambda: icp)
        return env

    def _patch_token(self, token="test.access.token"):
        return patch(
            "odoo.addons.ocr_techdata.services.mars_client.jwt_auth.get_access_token",
            return_value=token,
        )

    def test_successful_extract_returns_dict(self):
        env = self._make_env_mock()
        good_response = {"status": "success", "fields": {"amount_total": 100.0}}
        with self._patch_token():
            with patch("requests.Session.post") as mock_post:
                mock_post.return_value = MagicMock(
                    status_code=200,
                    json=lambda: good_response,
                    raise_for_status=lambda: None,
                )
                result = mars_client.extract(env, "b64data==", "invoice", "application/pdf")

        self.assertIsNotNone(result)
        self.assertEqual(result["status"], "success")

    def test_no_token_returns_none(self):
        env = self._make_env_mock()
        with patch(
            "odoo.addons.ocr_techdata.services.mars_client.jwt_auth.get_access_token",
            return_value=None,
        ):
            result = mars_client.extract(env, "b64data==", "invoice", "application/pdf")
        self.assertIsNone(result)

    def test_timeout_returns_none(self):
        env = self._make_env_mock()
        with self._patch_token():
            with patch("requests.Session.post", side_effect=requests.exceptions.Timeout):
                result = mars_client.extract(env, "b64data==", "invoice", "application/pdf")
        self.assertIsNone(result)

    def test_connection_error_returns_none(self):
        env = self._make_env_mock()
        with self._patch_token():
            with patch("requests.Session.post", side_effect=requests.exceptions.ConnectionError):
                result = mars_client.extract(env, "b64data==", "invoice", "application/pdf")
        self.assertIsNone(result)

    def test_http_500_returns_none(self):
        env = self._make_env_mock()
        with self._patch_token():
            with patch("requests.Session.post") as mock_post:
                mock_resp = MagicMock()
                mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
                    response=MagicMock(status_code=500)
                )
                mock_post.return_value = mock_resp
                result = mars_client.extract(env, "b64data==", "invoice", "application/pdf")
        self.assertIsNone(result)

    def test_unexpected_exception_returns_none(self):
        env = self._make_env_mock()
        with self._patch_token():
            with patch("requests.Session.post", side_effect=RuntimeError("boom")):
                result = mars_client.extract(env, "b64data==", "invoice", "application/pdf")
        self.assertIsNone(result)

    def test_request_includes_bearer_token(self):
        env = self._make_env_mock()
        with self._patch_token("mytoken123"):
            with patch("requests.Session.post") as mock_post:
                mock_post.return_value = MagicMock(
                    status_code=200,
                    json=lambda: {"status": "success", "fields": {}},
                    raise_for_status=lambda: None,
                )
                mars_client.extract(env, "b64data==", "invoice", "application/pdf")
                call_kwargs = mock_post.call_args[1]
                self.assertEqual(
                    call_kwargs["headers"]["Authorization"],
                    "Bearer mytoken123",
                )


class TestCreditOnMars(TransactionCase):
    def _make_env(self, admin_key="secret-admin"):
        icp = MagicMock()
        icp.get_param.side_effect = lambda key, default="": {
            "ocr_techdata.mars_admin_key": admin_key,
        }.get(key, default)
        env = MagicMock()
        env.__getitem__.return_value = MagicMock(sudo=lambda: icp)
        return env

    def test_credit_on_mars_success(self):
        env = self._make_env()
        with patch("requests.post") as mock_post:
            mock_post.return_value = MagicMock(
                status_code=200,
                raise_for_status=lambda: None,
                json=lambda: {"new_balance": 110},
            )
            result = mars_client.credit_on_mars(env, "odoo-prod", 100, note="Achat test")
        self.assertTrue(result)
        call_kwargs = mock_post.call_args
        self.assertIn("X-Admin-Key", call_kwargs[1]["headers"])

    def test_credit_on_mars_missing_admin_key_returns_false(self):
        env = self._make_env(admin_key="")
        result = mars_client.credit_on_mars(env, "odoo-prod", 100)
        self.assertFalse(result)

    def test_credit_on_mars_http_403_returns_false(self):
        env = self._make_env()
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.raise_for_status.side_effect = requests.exceptions.HTTPError(
                response=MagicMock(status_code=403)
            )
            mock_post.return_value = mock_resp
            result = mars_client.credit_on_mars(env, "odoo-prod", 100)
        self.assertFalse(result)

    def test_credit_on_mars_exception_returns_false(self):
        env = self._make_env()
        with patch("requests.post", side_effect=ConnectionError("unreachable")):
            result = mars_client.credit_on_mars(env, "odoo-prod", 100)
        self.assertFalse(result)
