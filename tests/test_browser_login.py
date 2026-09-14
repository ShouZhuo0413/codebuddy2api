import json
import unittest
from unittest.mock import Mock

import httpx
from fastapi import HTTPException

from admin.browser_login import BASE, BrowserLogin
import test_admin_server as admin_tests
from test_admin_server import credential


class BrowserLoginTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.time = 10000
        self.authorized = False
        self.bad_url = False
        self.bad_account = False
        self.calls = []
        self.saver = Mock(return_value={"account_id": "saved-id", "updated": False})
        def respond(req):
            self.calls.append(req)
            if req.url.path.endswith('/state'):
                return httpx.Response(200, json={"code": 0, "data": {"state": "upstream-secret", "authUrl": ('https://evil.example/login' if self.bad_url else BASE + '/login') + '?state=upstream-secret'}}, headers={"set-cookie": "login-session=test-cookie; Path=/"})
            self.assertIn('login-session=test-cookie', req.headers.get('cookie', ''))
            if req.url.path.endswith('/token'):
                if not self.authorized:
                    return httpx.Response(200, json={"code": 11217, "msg": "login ing"})
                return httpx.Response(200, json={"code": 0, "data": {"accessToken": "private-access", "refreshToken": "private-refresh", "expiresIn": 3600, "domain": "www.codebuddy.cn"}})
            self.assertEqual(req.headers['authorization'], 'Bearer private-access')
            return httpx.Response(200, json={"code": 0, "data": {} if self.bad_account else {"uid": "test-uid", "nickname": "测试授权账号"}})
        self.manager = BrowserLogin(self.saver, lambda: httpx.AsyncClient(transport=httpx.MockTransport(respond)), lambda: self.time)

    async def asyncTearDown(self):
        await self.manager.close()

    async def test_pending_success_idempotent_and_tokens_not_exposed(self):
        flow = await self.manager.start('owner', 'My account')
        self.assertNotEqual(flow['id'], 'upstream-secret')
        self.assertEqual((await self.manager.poll(flow['id'], 'owner'))['status'], 'pending')
        self.authorized = True
        self.time += 4
        result = await self.manager.poll(flow['id'], 'owner')
        self.assertEqual(result['status'], 'success')
        self.assertNotIn('private-', json.dumps(result))
        self.assertEqual(self.saver.call_args.args[0]['auth']['expiresAt'], 13604000)
        self.assertEqual(self.saver.call_args.args[1], 'My account')
        self.assertEqual(await self.manager.poll(flow['id'], 'owner'), result)
        self.saver.assert_called_once()

    async def test_owner_expiry_cancel_and_restart(self):
        flow = await self.manager.start('a', None)
        with self.assertRaises(HTTPException) as context:
            await self.manager.poll(flow['id'], 'b')
        self.assertEqual(context.exception.status_code, 404)
        self.time += 301
        self.assertEqual((await self.manager.poll(flow['id'], 'a'))['status'], 'expired')
        flow = await self.manager.start('a', None)
        replacement = await self.manager.start('a', None)
        self.assertNotIn(flow['id'], self.manager.flows)
        await self.manager.cancel(replacement['id'], 'a')
        self.assertFalse(self.manager.flows)
        self.saver.assert_not_called()

    async def test_untrusted_url_and_missing_account_are_rejected(self):
        self.bad_url = True
        with self.assertRaises(HTTPException):
            await self.manager.start('a', None)
        self.assertFalse(self.manager.flows)
        self.bad_url = False
        flow = await self.manager.start('a', None)
        self.authorized = True
        self.bad_account = True
        with self.assertRaises(HTTPException):
            await self.manager.poll(flow['id'], 'a')
        self.saver.assert_not_called()
        self.bad_account = False
        self.time += 4
        self.assertEqual((await self.manager.poll(flow['id'], 'a'))['status'], 'success')
        token_calls = [req for req in self.calls if req.url.path.endswith('/token')]
        self.assertEqual(len(token_calls), 1)


class BrowserAccountTests(unittest.IsolatedAsyncioTestCase):
    asyncSetUp = admin_tests.AdminTests.asyncSetUp
    asyncTearDown = admin_tests.AdminTests.asyncTearDown
    make_app = admin_tests.AdminTests.make_app
    login = admin_tests.AdminTests.login
    async def test_browser_account_deduplicates_and_preserves_active(self):
        store = self.app.state.store
        first = store.data['active']
        r = store.save_browser_account(credential('two'), 'Browser account')
        self.assertFalse(r['updated'])
        self.assertEqual(store.data['active'], first)
        r2 = store.save_browser_account(credential('two', 'Updated'), None)
        self.assertTrue(r2['updated'])
        self.assertEqual(r['account_id'], r2['account_id'])
        self.assertEqual(len(store.data['accounts']), 2)

    async def test_oauth_routes_enforce_session_and_csrf(self):
        self.assertEqual((await self.client.post('/admin/api/oauth/start', json={})).status_code, 401)
        await self.login()
        self.client.headers.pop('X-CSRF-Token')
        self.assertEqual((await self.client.post('/admin/api/oauth/start', json={})).status_code, 403)
        self.assertEqual((await self.client.post('/admin/api/oauth/fake/poll')).status_code, 403)
        self.assertEqual((await self.client.delete('/admin/api/oauth/fake')).status_code, 403)


if __name__ == '__main__':
    unittest.main()
