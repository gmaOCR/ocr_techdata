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

    def _make_env(self, database_uuid="test-db-uuid-1234", client_id_existing="", provider=""):
        icp = MagicMock()
        params = {
            "database.uuid": database_uuid,
            "ocr_techdata.client_id": client_id_existing,
            "ocr_techdata.provider": provider,
        }
        icp.get_param.side_effect = lambda key, default="": params.get(key, default)
        icp.set_param = MagicMock()
        env = MagicMock()
        env.__getitem__.return_value = MagicMock(sudo=lambda: icp)
        return env, icp

    def _set_param_keys(self, icp):
        return [c[0][0] for c in icp.set_param.call_args_list]

    def _assert_no_credentials(self, icp):
        keys = self._set_param_keys(icp)
        self.assertNotIn("ocr_techdata.client_id", keys)
        self.assertNotIn("ocr_techdata.client_secret", keys)

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

    def test_hook_sets_techdata_as_default_provider(self):
        """Installing the module makes Techdata OCR the default provider."""
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env()
        mock_response = MagicMock(json=lambda: {"client_id": "x", "client_secret": "y", "is_new": True})
        mock_response.raise_for_status = MagicMock()

        with patch("requests.post", return_value=mock_response):
            post_init_hook(env)

        set_calls = {c[0][0]: c[0][1] for c in icp.set_param.call_args_list}
        self.assertEqual(set_calls.get("ocr_techdata.provider"), "paddlevl")

    def test_hook_keeps_existing_provider_choice(self):
        """A persisted provider choice (reinstall) must not be overwritten."""
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env(client_id_existing="set", provider="odoo_iap")

        with patch("requests.post"):
            post_init_hook(env)

        self.assertNotIn("ocr_techdata.provider", self._set_param_keys(icp))

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

        self._assert_no_credentials(icp)

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

        self._assert_no_credentials(icp)

    def test_hook_skips_registration_during_test_builds(self):
        """odoo.sh dev/CI builds run with test_enable → no /auth/register call at all,
        so the rate-limited endpoint never pollutes the build log. The provider default
        is still applied (local param, no network)."""
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env()

        with patch("odoo.addons.ocr_techdata.hooks.config", {"test_enable": True}), \
                patch("requests.post") as mock_post:
            post_init_hook(env)

        mock_post.assert_not_called()
        self._assert_no_credentials(icp)
        self.assertIn("ocr_techdata.provider", self._set_param_keys(icp))

    @mute_logger(_HOOKS_LOGGER)
    def test_hook_skips_when_database_uuid_missing(self):
        from odoo.addons.ocr_techdata.hooks import post_init_hook

        env, icp = self._make_env(database_uuid="")

        with patch("requests.post") as mock_post:
            post_init_hook(env)

        mock_post.assert_not_called()


class TestRegisterInstanceAction(TransactionCase):
    """Tests for the manual 'Register' button in settings (action_register_instance)."""

    def _settings(self):
        return self.env["res.config.settings"].create({})

    def test_action_returns_success_notification(self):
        settings = self._settings()
        with patch("odoo.addons.ocr_techdata.hooks.register_instance",
                   return_value=(True, "Instance registered. 10 token(s) granted.")):
            result = settings.action_register_instance()
        self.assertEqual(result["tag"], "display_notification")
        self.assertEqual(result["params"]["type"], "success")
        self.assertIn("registered", result["params"]["message"])

    def test_action_returns_warning_on_failure(self):
        settings = self._settings()
        with patch("odoo.addons.ocr_techdata.hooks.register_instance",
                   return_value=(False, "Cannot reach the OCR server. Try again later.")):
            result = settings.action_register_instance()
        self.assertEqual(result["params"]["type"], "warning")
