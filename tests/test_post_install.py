"""Tests for post_init_hook — instance auto-registration."""
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase
from odoo.tools import mute_logger

_HOOKS_LOGGER = "odoo.addons.ocr_techdata.hooks"


class TestPostInstallHook(TransactionCase):
    """Tests for hooks.post_init_hook — requires no real network."""

    def setUp(self):
        super().setUp()
        # Odoo runs these tests with config['test_enable']=True, which the hook now
        # uses to skip auto-registration. Force it off so the registration branches
        # below are actually exercised. The dedicated skip test overrides this.
        patcher = patch("odoo.addons.ocr_techdata.hooks.config", {"test_enable": False})
        patcher.start()
        self.addCleanup(patcher.stop)

    def _make_env(self, database_uuid="test-db-uuid-1234", client_id_existing=""):
        icp = MagicMock()
        params = {
            "database.uuid": database_uuid,
            "ocr_techdata.client_id": client_id_existing,
        }
        icp.get_param.side_effect = lambda key, default="": params.get(key, default)
        icp.set_param = MagicMock()
        env = MagicMock()
        env.__getitem__.return_value = MagicMock(sudo=lambda: icp)
        return env, icp

    def test_hook_calls_register_and_stores_credentials(self):
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env()
        mock_response = MagicMock(
            status_code=200,
            json=lambda: {
                "client_id": "test-db-uuid-1234",
                "client_secret": "generated-secret-xyz",
                "credits_granted": 10,
                "is_new": True,
            },
        )
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response) as mock_post:
            post_init_hook(env)

        mock_post.assert_called_once()
        call_json = mock_post.call_args[1]["json"]
        self.assertEqual(call_json["database_uuid"], "test-db-uuid-1234")

        set_calls = {c[0][0]: c[0][1] for c in icp.set_param.call_args_list}
        self.assertIn("ocr_techdata.client_id", set_calls)
        self.assertIn("ocr_techdata.client_secret", set_calls)
        self.assertEqual(set_calls["ocr_techdata.client_id"], "test-db-uuid-1234")
        self.assertEqual(set_calls["ocr_techdata.client_secret"], "generated-secret-xyz")

    def test_hook_skips_if_client_id_already_set(self):
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env(client_id_existing="already-set")

        with patch("requests.post") as mock_post:
            post_init_hook(env)

        mock_post.assert_not_called()

    @mute_logger(_HOOKS_LOGGER)
    def test_hook_does_not_block_on_connection_error(self):
        from odoo.addons.ocr_techdata.hooks import post_init_hook
        import requests

        env, icp = self._make_env()

        with patch("requests.post", side_effect=requests.exceptions.ConnectionError("down")):
            # Must not raise — installation should succeed even if mars is unreachable
            try:
                post_init_hook(env)
            except Exception as exc:
                self.fail(f"post_init_hook raised unexpectedly: {exc}")

        icp.set_param.assert_not_called()

    @mute_logger(_HOOKS_LOGGER)
    def test_hook_does_not_block_on_timeout(self):
        from odoo.addons.ocr_techdata.hooks import post_init_hook
        import requests

        env, icp = self._make_env()

        with patch("requests.post", side_effect=requests.exceptions.Timeout("timeout")):
            try:
                post_init_hook(env)
            except Exception as exc:
                self.fail(f"post_init_hook raised unexpectedly: {exc}")

    @mute_logger(_HOOKS_LOGGER)
    def test_hook_does_not_block_on_http_error(self):
        """A 429/5xx from the register endpoint must be a WARNING + skip, never an
        ERROR traceback (odoo.sh CI fails the build on ERROR log lines)."""
        from odoo.addons.ocr_techdata.hooks import post_init_hook
        import requests

        env, icp = self._make_env()
        mock_response = MagicMock(status_code=429)
        mock_response.raise_for_status = MagicMock(
            side_effect=requests.exceptions.HTTPError("429", response=mock_response)
        )

        with patch("requests.post", return_value=mock_response):
            try:
                post_init_hook(env)
            except Exception as exc:
                self.fail(f"post_init_hook raised unexpectedly: {exc}")

        icp.set_param.assert_not_called()

    def test_hook_skips_during_test_builds(self):
        """odoo.sh dev/CI builds run with test_enable → no registration attempt at all,
        so the rate-limited /auth/register endpoint never pollutes the build log."""
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env()

        with patch("odoo.addons.ocr_techdata.hooks.config", {"test_enable": True}), \
                patch("requests.post") as mock_post:
            post_init_hook(env)

        mock_post.assert_not_called()
        icp.set_param.assert_not_called()

    @mute_logger(_HOOKS_LOGGER)
    def test_hook_skips_when_database_uuid_missing(self):
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env(database_uuid="")

        with patch("requests.post") as mock_post:
            post_init_hook(env)

        mock_post.assert_not_called()
