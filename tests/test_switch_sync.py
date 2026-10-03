import json
import tempfile
import time
import unittest
from pathlib import Path

from admin.server import Store


def entry(uid, name, access="access-token", refresh="refresh-token", expires_delta=3600000):
    return {
        "access_token": access,
        "refresh_token": refresh,
        "token_type": "Bearer",
        "domain": "www.codebuddy.cn",
        "uid": uid,
        "nickname": name,
        "expiresAt": int(time.time() * 1000) + expires_delta,
        "refreshExpiresAt": int(time.time() * 1000) + 30 * 86400000,
        "refreshedAt": int(time.time() * 1000),
        "auth_raw": {"accessToken": "stale-access", "refreshToken": "stale-refresh", "domain": "www.codebuddy.cn", "expiresAt": 1},
        "profile_raw": {"uid": uid, "nickname": name, "phoneNumber": "13800000000"},
    }


class SwitchSyncTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root / "management", self.root / "auth", "client-key", "admin-key-long-enough-for-tests")
        self.library = self.root / "accounts.json"

    def tearDown(self):
        self.temp.cleanup()

    def write_library(self, entries):
        self.library.write_text(json.dumps(entries, ensure_ascii=False), encoding="utf-8")

    def accounts(self):
        return self.store.data["accounts"]

    def test_imports_new_accounts_and_is_idempotent(self):
        self.write_library([entry("u1", "一号"), entry("u2", "二号")])
        result = self.store.sync_switch_accounts(self.library)
        self.assertEqual((result["imported"], result["updated"], result["failed"]), (2, 0, 0))
        self.assertEqual(len(self.accounts()), 2)
        self.assertEqual(sorted(item["name"] for item in self.accounts().values()), ["一号", "二号"])
        # 顶层 token 覆盖 auth_raw 里的旧值
        aid = next(aid for aid, item in self.accounts().items() if item["name"] == "一号")
        doc = json.loads(self.store.file_for(self.accounts()[aid]).read_text(encoding="utf-8"))
        self.assertEqual(doc["auth"]["accessToken"], "access-token")
        self.assertEqual(doc["auth"]["domain"], "www.codebuddy.cn")
        self.assertEqual(doc["account"]["nickname"], "一号")
        self.assertIn(self.store.data["active"], self.accounts())
        # 重复同步不重复导入
        again = self.store.sync_switch_accounts(self.library)
        self.assertEqual((again["imported"], again["skipped"]), (0, 2))
        self.assertEqual(self.store.switch_sync["skipped"], 2)
        self.assertIsNone(self.store.switch_sync["reason"])

    def test_adopts_refreshed_token_and_keeps_local_settings(self):
        self.write_library([entry("u1", "一号")])
        self.store.sync_switch_accounts(self.library)
        aid = next(iter(self.accounts()))
        self.store.data["accounts"][aid]["name"] = "我的备注"
        self.store.data["accounts"][aid]["enabled"] = False
        status = self.store.data.setdefault("account_status", {}).setdefault(aid, {})
        status.update(remaining=123.5, expiring_at=1800000000, request_count=7)

        self.write_library([entry("u1", "一号", access="access-token-2", refresh="refresh-token-2", expires_delta=7200000)])
        result = self.store.sync_switch_accounts(self.library)
        self.assertEqual((result["imported"], result["updated"], result["skipped"]), (0, 1, 0))
        item = self.accounts()[aid]
        self.assertEqual(item["name"], "我的备注")           # 本地备注不被覆盖
        self.assertFalse(item["enabled"])                     # 暂停状态不被复活
        status = self.store.data["account_status"][aid]
        self.assertEqual(status["remaining"], 123.5)          # 积分缓存不被清空
        self.assertEqual(status["request_count"], 7)
        doc = json.loads(self.store.file_for(item).read_text(encoding="utf-8"))
        self.assertEqual(doc["auth"]["accessToken"], "access-token-2")
        self.assertEqual(doc["account"]["nickname"], "一号")   # 账号身份仍来自 switch
        # 拿新 token 的 manager 缓存已失效
        self.assertIsNone(self.store.manager_for(aid, item)._cached)

    def test_keeps_the_newer_local_token_instead_of_rolling_back(self):
        self.write_library([entry("u1", "一号")])
        self.store.sync_switch_accounts(self.library)
        aid = next(iter(self.accounts()))
        item = self.accounts()[aid]
        # 池侧自主刷新过：本地凭据比账号库更新，同步不得覆盖
        doc = json.loads(self.store.file_for(item).read_text(encoding="utf-8"))
        later = int(time.time() * 1000) + 60000
        doc["auth"].update(accessToken="local-newer-access", refreshToken="local-newer-refresh",
                           lastRefreshTime=later, expiresAt=later + 86400000)
        self.store.file_for(item).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")

        result = self.store.sync_switch_accounts(self.library)
        self.assertEqual((result["updated"], result["stale"]), (0, 1))
        kept = json.loads(self.store.file_for(item).read_text(encoding="utf-8"))
        self.assertEqual(kept["auth"]["accessToken"], "local-newer-access")
        self.assertEqual(self.store.switch_sync["stale"], 1)

        # 时间戳完全相同但 token 不同：无法判定更新，保守保留本地
        published = json.loads(self.library.read_text(encoding="utf-8"))[0]
        doc["auth"]["lastRefreshTime"] = published["refreshedAt"]
        doc["auth"]["expiresAt"] = published["expiresAt"]
        self.store.file_for(item).write_text(json.dumps(doc, ensure_ascii=False), encoding="utf-8")
        again = self.store.sync_switch_accounts(self.library)
        self.assertEqual((again["updated"], again["stale"]), (0, 1))
        self.assertEqual(json.loads(self.store.file_for(item).read_text(encoding="utf-8"))["auth"]["accessToken"], "local-newer-access")

    def test_invalid_entries_and_missing_library_never_raise(self):
        self.write_library([entry("u1", "一号"), {"uid": "u2", "nickname": "缺 token"}, "不是对象", {"uid": "u1", "nickname": "重复", "auth_raw": {"accessToken": "a", "refreshToken": "b"}, "access_token": "a", "refresh_token": "b", "expiresAt": int(time.time() * 1000)}])
        result = self.store.sync_switch_accounts(self.library)
        self.assertEqual(result["imported"], 1)
        self.assertEqual(result["failed"], 2)     # 缺 token 与非法条目
        self.assertEqual(result["skipped"], 1)    # 重复 uid 去重
        self.assertEqual(len(self.accounts()), 1)

        missing = self.store.sync_switch_accounts(self.root / "不存在.json")
        self.assertEqual(missing["imported"], 0)
        self.assertEqual(missing["reason"], "账号库不存在或不可读")

        broken = self.root / "broken.json"
        broken.write_text("{不是 JSON", encoding="utf-8")
        self.assertEqual(self.store.sync_switch_accounts(broken)["reason"], "账号库格式异常")

    def test_capacity_limit_marks_failure_instead_of_growing(self):
        with self.store.lock:
            for index in range(100):
                self.store.data["accounts"]["pad%03d" % index] = {"file": "pad%03d.info" % index, "name": "占用%03d" % index, "created": 0, "enabled": True}
        self.write_library([entry("u1", "一号")])
        result = self.store.sync_switch_accounts(self.library)
        self.assertEqual((result["imported"], result["failed"]), (0, 1))
        self.assertEqual(len(self.accounts()), 100)
