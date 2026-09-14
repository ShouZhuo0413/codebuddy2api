"""CodeBuddy CN browser authorization. Tokens never cross the management API."""
import asyncio
import math
import secrets
import time
from urllib.parse import parse_qs, urlsplit

import httpx
from fastapi import HTTPException

BASE = "https://copilot.tencent.com"
HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/plain, */*",
    "X-Requested-With": "XMLHttpRequest",
    "Origin": "https://www.codebuddy.cn",
    "Referer": "https://www.codebuddy.cn/",
    "User-Agent": "CLI/2.63.2 CodeBuddy/2.63.2",
}
TTL = 300


class BrowserLogin:
    def __init__(self, save_account, client_factory=None, clock=time.time):
        self.save_account = save_account
        self.client_factory = client_factory or (lambda: httpx.AsyncClient(headers=HEADERS, timeout=20, follow_redirects=False))
        self.clock = clock
        self.flows = {}
        self.guard = asyncio.Lock()

    async def _discard(self, fid):
        flow = self.flows.pop(fid, None)
        if flow:
            await flow["client"].aclose()

    async def cleanup(self):
        for fid, flow in list(self.flows.items()):
            if flow["expires"] <= self.clock() and not flow["lock"].locked():
                await self._discard(fid)

    async def close(self):
        for fid in list(self.flows):
            await self._discard(fid)

    async def cancel_owner(self, owner):
        for fid, flow in list(self.flows.items()):
            if flow["owner"] == owner:
                async with flow["lock"]:
                    await self._discard(fid)

    @staticmethod
    def envelope(response):
        if response.status_code != 200:
            raise HTTPException(502, "授权服务暂时不可用，请稍后重试")
        try:
            obj = response.json()
        except ValueError:
            raise HTTPException(502, "授权服务返回格式异常，请重新生成链接")
        if not isinstance(obj, dict):
            raise HTTPException(502, "授权服务返回格式异常")
        return obj

    async def start(self, owner, name):
        async with self.guard:
            await self.cleanup()
            if len(self.flows) >= 16:
                raise HTTPException(429, "等待授权的请求过多，请稍后重试")
            await self.cancel_owner(owner)
            client = self.client_factory()
            try:
                obj = self.envelope(await client.post(BASE + "/v2/plugin/auth/state", params={"platform": "CLI"}, json={}))
                data = obj.get("data") or {}
                if obj.get("code") != 0 or not isinstance(data, dict):
                    raise HTTPException(502, "无法生成授权链接，请稍后重试")
                state, url = data.get("state"), data.get("authUrl")
                if not isinstance(state, str) or not 1 <= len(state) <= 2048 or not isinstance(url, str) or len(url) > 8192:
                    raise HTTPException(502, "授权服务缺少有效的登录信息")
                parts = urlsplit(url)
                if (parts.scheme != "https" or parts.hostname != "copilot.tencent.com" or parts.port not in (None, 443)
                        or parts.username or parts.password or parts.path != "/login"
                        or parse_qs(parts.query).get("state") != [state]):
                    raise HTTPException(502, "授权链接校验失败，请重新生成")
                fid = secrets.token_urlsafe(24)
                flow = {"owner": owner, "state": state, "client": client, "name": name,
                        "expires": self.clock() + TTL, "lock": asyncio.Lock(), "next_poll": 0,
                        "tokens": None, "saved": None}
                self.flows[fid] = flow
                return {"id": fid, "url": url, "expires_at": int(flow["expires"] * 1000), "interval": 3}
            except BaseException:
                await client.aclose()
                raise

    def get(self, fid, owner):
        flow = self.flows.get(fid)
        if not flow or flow["owner"] != owner:
            raise HTTPException(404, "授权会话不存在，请重新生成登录链接")
        return flow

    async def cancel(self, fid, owner):
        flow = self.get(fid, owner)
        async with flow["lock"]:
            await self._discard(fid)
        return {"status": "cancelled"}

    async def poll(self, fid, owner):
        flow = self.get(fid, owner)
        if flow["lock"].locked():
            return {"status": "pending"}
        async with flow["lock"]:
            if fid not in self.flows:
                raise HTTPException(404, "授权会话已取消")
            if flow["expires"] <= self.clock():
                await self._discard(fid)
                return {"status": "expired"}
            if flow["saved"]:
                return {"status": "success", **flow["saved"]}
            if flow["next_poll"] > self.clock():
                return {"status": "pending"}
            flow["next_poll"] = self.clock() + 3
            client = flow["client"]
            if flow["tokens"] is None:
                obj = self.envelope(await client.get(BASE + "/v2/plugin/auth/token", params={"state": flow["state"]}))
                if obj.get("code") == 11217:
                    return {"status": "pending"}
                if obj.get("code") != 0:
                    raise HTTPException(502, "上游授权未成功，请重新生成链接并登录")
                tokens = obj.get("data")
                if not isinstance(tokens, dict) or not tokens.get("accessToken") or not tokens.get("refreshToken"):
                    raise HTTPException(502, "授权服务没有返回完整凭据，请重新登录")
                flow["tokens"] = tokens
            tok = flow["tokens"]
            obj = self.envelope(await client.get(BASE + "/v2/plugin/login/account", params={"state": flow["state"]}, headers={"Authorization": "Bearer " + tok["accessToken"]}))
            acct = obj.get("data")
            if obj.get("code") != 0 or not isinstance(acct, dict) or not acct.get("uid"):
                raise HTTPException(502, "授权已完成，但账号信息暂未获取成功，请稍后重试")
            auth = {k: tok[k] for k in ["accessToken", "refreshToken", "domain"] if k in tok}
            if "workbuddy.ai" in str(auth.get("domain", "")).lower():
                raise HTTPException(400, "当前服务仅支持国内 CodeBuddy / WorkBuddy 账号")
            expiry = tok.get("expiresAt")
            if isinstance(expiry, (int, float)) and math.isfinite(expiry) and expiry > 0:
                auth["expiresAt"] = int(expiry * 1000 if expiry < 100000000000 else expiry)
            else:
                seconds = tok.get("expiresIn")
                if not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
                    raise HTTPException(502, "授权服务没有返回有效的令牌到期时间")
                auth["expiresAt"] = int((self.clock() + seconds) * 1000)
            if flow["expires"] <= self.clock():
                await self._discard(fid)
                return {"status": "expired"}
            doc = {"account": {k: acct.get(k, "") for k in ["uid", "enterpriseId", "nickname"]}, "auth": auth}
            flow["saved"] = self.save_account(doc, flow["name"])
            flow["tokens"] = None
            flow["state"] = ""
            await client.aclose()
            return {"status": "success", **flow["saved"]}
