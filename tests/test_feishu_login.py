"""Mock tests for feishu_login — covers core flow and error paths.

Tests lark_oapi.aregister_app integration with AccountStore,
verifying QR callback trigger, credential persistence, and all
three error paths (timeout, access denied, expired).
"""

import asyncio
import sys
from unittest.mock import AsyncMock, MagicMock, patch

# Mock lark_oapi before any src imports — the __init__.py imports
# FeishuAdapter which imports lark_oapi.channel. We provide a minimal
# stub so collection doesn't fail on ModuleNotFoundError.
_LARK_MOCK = MagicMock()
_LARK_MOCK.channel.types.Conversation = MagicMock
_LARK_MOCK.channel.types.Identity = MagicMock
_LARK_MOCK.channel.types.InboundMessage = MagicMock
_LARK_MOCK.channel.types.Mention = MagicMock
sys.modules["lark_oapi"] = _LARK_MOCK
sys.modules["lark_oapi.channel"] = _LARK_MOCK.channel
sys.modules["lark_oapi.channel.types"] = _LARK_MOCK.channel.types

import pytest

_project_root = __file__.rsplit("/", 2)[0]
if _project_root not in sys.path:
    sys.path.insert(0, _project_root)

from src.star_chain.account_store import AccountCredential, AccountStore
from src.star_chain.feishu_login import feishu_login

# ---------------------------------------------------------------------------
# fixtures
# ---------------------------------------------------------------------------

SAMPLE_RESULT = {
    "client_id": "cli_test_app_001",
    "client_secret": "sec_test_secret_xyz",
    "user_info": {"name": "测试用户", "open_id": "ou_test_user_001"},
}


@pytest.fixture(autouse=True)
def enable_lark():
    """Force HAS_LARK=True so feishu_login doesn't short-circuit."""
    with patch("src.star_chain.feishu_login.HAS_LARK", True):
        yield


@pytest.fixture
def mock_lark():
    """Mock the lark module reference in feishu_login.

    Provides:
      - aregister_app: AsyncMock returning SAMPLE_RESULT by default
      - AppAccessDeniedError, AppExpiredError: real Exception subclasses
    """
    with patch("src.star_chain.feishu_login.lark") as m:
        m.aregister_app = AsyncMock(return_value=SAMPLE_RESULT)
        m.AppAccessDeniedError = type("AppAccessDeniedError", (Exception,), {})
        m.AppExpiredError = type("AppExpiredError", (Exception,), {})
        yield m


@pytest.fixture
def mock_store():
    """Return a MagicMock AccountStore configured for success."""
    store = MagicMock(spec=AccountStore)
    store.load_account.return_value = None
    store.save_credential.return_value = AccountCredential(
        account_id="cli_test_app_001",
        credential_type="feishu",
        app_secret="sec_test_secret_xyz",
        domain="feishu.cn",
        user_name="测试用户",
        user_id="ou_test_user_001",
        saved_at="2026-01-01T00:00:00Z",
    )
    return store


# ---------------------------------------------------------------------------
# 1 — success path
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_success_path(mock_lark, mock_store):
    """Mock aregister_app returns SAMPLE_RESULT; credential saved to AccountStore."""
    cred = await feishu_login(store=mock_store, json_mode=True)

    assert cred is not None
    assert cred.account_id == "cli_test_app_001"
    assert cred.app_secret == "sec_test_secret_xyz"
    assert cred.credential_type == "feishu"
    assert cred.user_name == "测试用户"

    # Confirm aregister_app was called with the right kwargs
    mock_lark.aregister_app.assert_awaited_once()
    call_kwargs = mock_lark.aregister_app.await_args[1]
    assert "on_qr_code" in call_kwargs
    assert "on_status_change" in call_kwargs
    assert call_kwargs.get("domain") == "https://accounts.feishu.cn"

    # Confirm credential saved to store
    mock_store.save_credential.assert_called_once()
    saved_cred: AccountCredential = mock_store.save_credential.call_args[0][0]
    assert saved_cred.account_id == "cli_test_app_001"
    assert saved_cred.app_secret == "sec_test_secret_xyz"
    assert saved_cred.credential_type == "feishu"
    assert saved_cred.domain == "feishu.cn"
    assert saved_cred.user_name == "测试用户"
    assert saved_cred.user_id == "ou_test_user_001"


# ---------------------------------------------------------------------------
# 2 — QR callback is triggered
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_qr_callback_triggered(mock_lark, mock_store):
    """on_qr_code callback is passed to aregister_app and fires _render_qr."""
    with patch("src.star_chain.feishu_login._render_qr") as mock_render:
        cred = await feishu_login(store=mock_store, json_mode=True)

        assert cred is not None
        assert mock_lark.aregister_app.await_count == 1

        # Extract the on_qr_code callback that was passed to aregister_app.
        call_kwargs = mock_lark.aregister_app.call_args[1]
        on_qr_code = call_kwargs["on_qr_code"]
        assert callable(on_qr_code)

        # Manually invoke the callback — still inside the patch context
        on_qr_code({"url": "https://test.feishu.cn/qr/abc123"})

        # _render_qr should have been called with the QR URL
        mock_render.assert_called_once_with(
            "https://test.feishu.cn/qr/abc123",
            qrcode_url="https://test.feishu.cn/qr/abc123",
            json_mode=True,
        )


# ---------------------------------------------------------------------------
# 3 — credential not overwritten when --replace is False
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_replace_returns_existing(mock_lark, mock_store):
    """When load_account returns existing and replace=False, return existing without saving."""
    existing = AccountCredential(
        account_id="cli_test_app_001",
        credential_type="feishu",
        app_secret="existing_secret",
        user_name="老用户",
    )
    mock_store.load_account.return_value = existing

    cred = await feishu_login(store=mock_store, json_mode=True, replace=False)

    assert cred is not None
    assert cred.app_secret == "existing_secret"
    assert cred.user_name == "老用户"
    # save_credential should NOT be called for existing accounts
    mock_store.save_credential.assert_not_called()


# ---------------------------------------------------------------------------
# 4 — TimeoutError → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_timeout_returns_none(mock_lark, mock_store):
    """asyncio.TimeoutError from wait_for → feishu_login returns None."""
    mock_lark.aregister_app.side_effect = asyncio.TimeoutError()

    cred = await feishu_login(store=mock_store, json_mode=True)
    assert cred is None

    # save_credential should NOT be called on error
    mock_store.save_credential.assert_not_called()


# ---------------------------------------------------------------------------
# 5 — AppAccessDeniedError → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_access_denied_returns_none(mock_lark, mock_store):
    """lark.AppAccessDeniedError → feishu_login returns None."""
    mock_lark.aregister_app.side_effect = mock_lark.AppAccessDeniedError()

    cred = await feishu_login(store=mock_store, json_mode=True)
    assert cred is None

    mock_store.save_credential.assert_not_called()


# ---------------------------------------------------------------------------
# 6 — AppExpiredError → returns None
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_app_expired_returns_none(mock_lark, mock_store):
    """lark.AppExpiredError → feishu_login returns None."""
    mock_lark.aregister_app.side_effect = mock_lark.AppExpiredError()

    cred = await feishu_login(store=mock_store, json_mode=True)
    assert cred is None

    mock_store.save_credential.assert_not_called()


# ---------------------------------------------------------------------------
# 7 — lark-oapi not installed → returns None without calling aregister_app
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_lark_oapi_returns_none(mock_lark, mock_store):
    """HAS_LARK=False → feishu_login returns None without calling aregister_app."""
    with patch("src.star_chain.feishu_login.HAS_LARK", False):
        cred = await feishu_login(store=mock_store, json_mode=True)

    assert cred is None
    mock_lark.aregister_app.assert_not_called()
    mock_store.save_credential.assert_not_called()
