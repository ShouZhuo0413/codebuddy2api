"""使用端标识测试：验证发往后端的请求携带与真机一致的调用方身份。"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core import converter  # noqa: E402

# 真机抓包得到的请求头取值，是上游计费后台识别调用来源的依据
EXPECTED_CLIENT_HEADERS = {
    "X-IDE-Type": "CLI",
    "X-IDE-Name": "CLI",
    "X-IDE-Version": "2.159.0",
    "X-Product": "SaaS",
    "X-Requested-With": "XMLHttpRequest",
    "User-Agent": "CLI/2.159.0 CodeBuddy/2.159.0",
}


@pytest.fixture
def credential(tmp_path):
    """构造一个无需刷新令牌即可读取的凭据文件。"""
    session = {
        "auth": {
            "accessToken": "access-token-plain",
            "refreshToken": "refresh-token-plain",
            "domain": "www.codebuddy.cn",
            "expiresAt": 9999999999999,
        },
        "account": {"uid": "u1", "enterpriseId": "e1"},
    }
    path = tmp_path / "account.info"
    path.write_text(json.dumps(session), encoding="utf-8")
    return converter.CredentialManager(path)


def test_backend_headers_carry_client_identity(credential):
    """所有发往后端的请求都应带完整的调用方身份头，否则计费后台只显示为空。"""
    headers = credential.get_headers()
    for name, value in EXPECTED_CLIENT_HEADERS.items():
        assert headers[name] == value


def test_rebuilt_headers_keep_client_identity(credential):
    """令牌刷新后重建的请求头同样要包含身份标识，不能只在首次构建时带。"""
    session = credential._session()
    rebuilt = credential._build_headers_from(session["auth"], session["account"])
    for name, value in EXPECTED_CLIENT_HEADERS.items():
        assert rebuilt[name] == value


def test_user_agent_matches_client_version():
    """User-Agent 必须由使用端版本号推导，避免两者不一致而暴露身份矛盾。"""
    assert converter.USER_AGENT == (
        f"CLI/{converter.CLIENT_IDE_VERSION} CodeBuddy/{converter.CLIENT_IDE_VERSION}"
    )


def test_identity_constants_are_not_empty():
    """身份常量缺失会让上游无法归属调用来源，这里显式守住非空。"""
    for value in (
        converter.CLIENT_IDE_TYPE,
        converter.CLIENT_IDE_NAME,
        converter.CLIENT_IDE_VERSION,
        converter.CLIENT_PRODUCT,
        converter.USER_AGENT,
        converter.REQUESTED_WITH,
    ):
        assert isinstance(value, str) and value.strip()
