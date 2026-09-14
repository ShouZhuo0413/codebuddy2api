import asyncio
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock

import httpx
from fastapi import HTTPException

from core import converter
from admin.server import Store
from admin.pool import AccountPool, PoolMiddleware, REQUEST_CREDENTIAL, summarize_packages
from test_admin_server import credential


class PoolTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        root = Path(self.tmp.name)
        self.store = Store(root/'management', root/'auth', 'api-test', 'admin-long-enough-test-key')
        self.a = self.store.save_browser_account(credential('one'), 'one')['account_id']
        self.b = self.store.save_browser_account(credential('two'), 'two')['account_id']
        self.now = 1800000000
        self.checked = False
        self.checkin_posts = 0
        self.fail = False
        self.pages = []
        def respond(req):
            if self.fail:
                return httpx.Response(500)
            if req.url.path.endswith('checkin-activity-status'):
                return httpx.Response(200,json={'code':0,'data':{'active':True,'today_checked_in':self.checked}})
            if req.url.path.endswith('daily-checkin'):
                self.checkin_posts += 1; self.checked=True
                return httpx.Response(200,json={'code':0,'data':{}})
            body=json.loads(req.content)
            self.pages.append(body['PageNumber'])
            return httpx.Response(200,json={'code':0,'data':{'Response':{'Data':{'TotalCount':1,'Accounts':[{'CycleCapacityRemainPrecise':'479.59','CycleCapacitySize':500,'CapacityRemain':500}]}}}})
        self.pool=AccountPool(self.store,clock=lambda:self.now,client_factory=lambda:httpx.Client(transport=httpx.MockTransport(respond)))
        for aid,item in self.store.data['accounts'].items():
            self.store.manager_for(aid,item).get_headers=Mock(return_value={'X-Domain':'www.codebuddy.cn'})

    async def asyncTearDown(self):
        self.tmp.cleanup()

    def test_precise_cycle_balance_and_unknown(self):
        result=summarize_packages([{'CapacityRemain':500,'CapacitySize':500,'CycleCapacityRemainPrecise':'479.59','CycleCapacitySizePrecise':'500'},{'CapacityRemainPrecise':'0.12','CapacitySize':100}])
        self.assertEqual(result['remaining'],479.71)
        with self.assertRaises(ValueError): summarize_packages([{}])
        self.assertEqual(self.pool.state(self.store.account_rows()[0]),'available')

    def test_round_robin_paused_cooling_exhausted_and_manual(self):
        self.assertEqual([self.pool.select()[0] for _ in range(4)],[self.a,self.b,self.a,self.b])
        self.store.data['accounts'][self.a]['enabled']=False
        self.assertEqual(self.pool.select()[0],self.b)
        self.pool.record(self.b,429)
        with self.assertRaises(HTTPException):self.pool.select()
        self.now+=1801
        self.assertEqual(self.pool.select()[0],self.b)
        self.pool.update(self.b,remaining=0,packages=1,credits_updated=self.now)
        with self.assertRaises(HTTPException):self.pool.select()
        self.now+=601
        self.assertEqual(self.pool.select()[0],self.b)
        self.store.data['pool']['routing']='manual';self.store.data['active']=self.a
        with self.assertRaises(HTTPException):self.pool.select()

    async def test_checkin_idempotent_and_failure_retains_balance(self):
        a,b=await asyncio.gather(asyncio.to_thread(self.pool.operate,self.a,'checkin'),asyncio.to_thread(self.pool.operate,self.a,'checkin'))
        self.assertTrue(a['ok'] and b['ok']);self.assertEqual(self.checkin_posts,1)
        self.assertEqual(self.store.data['account_status'][self.a]['remaining'],479.59)
        self.fail=True
        result=await asyncio.to_thread(self.pool.operate,self.a,'status')
        self.assertFalse(result['ok']);self.assertEqual(self.store.data['account_status'][self.a]['remaining'],479.59)

    async def test_paused_excluded_from_batch_and_schedule_persists(self):
        self.store.data['accounts'][self.b]['enabled']=False
        self.store.data['pool']['checkin_time']='00:00'
        await self.pool.tick()
        self.assertEqual(self.checkin_posts,1)
        self.assertNotIn(self.b,self.store.data['account_status'])
        restarted=AccountPool(self.store,clock=lambda:self.now,client_factory=self.pool.client_factory)
        await restarted.tick()
        self.assertEqual(self.checkin_posts,1)
        data=json.loads(self.store.path.read_text(encoding='utf-8'))
        self.assertTrue(data['account_status'][self.a]['checkin_date'])

    async def test_context_isolation_concurrent_requests_and_auth(self):
        async def app(scope,receive,send):
            before=REQUEST_CREDENTIAL.get().path.name
            await asyncio.sleep(.01)
            after=REQUEST_CREDENTIAL.get().path.name
            data=json.dumps({'before':before,'after':after}).encode()
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'application/json')]})
            await send({'type':'http.response.body','body':data})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=PoolMiddleware(app,self.pool)),base_url='http://test') as client:
            self.assertEqual((await client.post('/v1/responses')).status_code,401)
            results=await asyncio.gather(*(client.post('/v1/responses',headers={'Authorization':'Bearer api-test'}) for _ in range(4)))
            files=[r.json()['before'] for r in results]
            self.assertEqual(len(set(files)),2)
            self.assertTrue(all(r.json()['before']==r.json()['after'] for r in results))
        self.assertIsNone(REQUEST_CREDENTIAL.get())

    async def test_sse_error_marks_cooldown_without_replay(self):
        calls=[]
        async def app(scope,receive,send):
            calls.append(1)
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
            for part in [b'data: {"error":',b'{"code":429}}\n\n']:
                await send({'type':'http.response.body','body':part,'more_body':True})
            await send({'type':'http.response.body','body':b''})
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=PoolMiddleware(app,self.pool)),base_url='http://test') as client:
            response=await client.post('/v1/messages',headers={'Authorization':'Bearer api-test'})
            self.assertEqual(response.status_code,200)
        self.assertEqual(len(calls),1)
        self.assertGreater(self.store.data['account_status'][self.a]['cooldown_until'],self.now)
        self.assertEqual(self.pool.select()[0],self.b)

    def test_pagination_is_complete(self):
        def handler(req):
            page=json.loads(req.content)['PageNumber']
            accounts=[{'CapacityRemainPrecise':'1.25','CapacitySize':2}]* (100 if page==1 else 1)
            return httpx.Response(200,json={'code':0,'data':{'Response':{'Data':{'TotalCount':101,'Accounts':accounts}}}})
        with httpx.Client(transport=httpx.MockTransport(handler)) as c:
            result=self.pool.credits(c,{})
        self.assertEqual(result['packages'],101)
        self.assertEqual(result['remaining'],126.25)


if __name__=='__main__':unittest.main()
