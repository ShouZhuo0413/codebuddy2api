import asyncio
import json
import unittest

from admin.metrics import MetricsMiddleware, RequestMetrics


class MetricsTests(unittest.IsolatedAsyncioTestCase):
    async def run_request(self, status=200, chunks=None, stream=False, source="api", path="/v1/responses", crash=False):
        metrics = RequestMetrics()
        chunks = chunks if chunks is not None else [b'{"ok":true}']
        sent = []
        async def app(scope, receive, send):
            self.assertEqual(metrics.snapshot()['in_flight'], int(path == '/v1/responses'))
            await send({'type':'http.response.start','status':status,'headers':[(b'content-type', b'text/event-stream' if stream else b'application/json')]})
            for chunk in chunks:
                await send({'type':'http.response.body','body':chunk,'more_body':True})
            if crash:
                raise RuntimeError('interrupted')
            await send({'type':'http.response.body','body':b'','more_body':False})
        async def receive():
            return {'type':'http.request','body':b''}
        async def send(message):
            sent.append(message)
        scope={'type':'http','method':'POST','path':path}
        middleware=MetricsMiddleware(app,metrics,source)
        if crash:
            with self.assertRaises(RuntimeError): await middleware(scope,receive,send)
        else:
            await middleware(scope,receive,send)
        self.assertEqual(b''.join(m.get('body',b'') for m in sent), b''.join(chunks))
        return metrics.snapshot()

    async def test_success_and_http_error(self):
        s=await self.run_request(); self.assertEqual(s['completed'],1);self.assertEqual(s['success_rate'],100);self.assertEqual(s['in_flight'],0)
        s=await self.run_request(status=401);self.assertEqual(s['failed'],1);self.assertEqual(s['http_success_rate'],0)

    async def test_stream_error_with_200_is_not_success(self):
        s=await self.run_request(stream=True,chunks=[b'data: {"err',b'or":{"code":429}}\n\ndata: [DONE]\n\n'])
        self.assertEqual(s['http_success_rate'],100);self.assertEqual(s['success_rate'],0)
        self.assertEqual(s['recent'][0]['outcome'],'stream_error')

    async def test_complete_and_incomplete_stream(self):
        s=await self.run_request(stream=True,chunks=[b'data: {"type":"response.completed"}\n\n'])
        self.assertEqual(s['success_rate'],100)
        s=await self.run_request(stream=True,chunks=[b'data: {"delta":"text"}\n\n'])
        self.assertEqual(s['success_rate'],0);self.assertEqual(s['recent'][0]['outcome'],'interrupted')
        s=await self.run_request(stream=True,chunks=[b'data: {"type":"response.completed","response":{"status":"failed"}}\n'])
        self.assertEqual(s['failed'],1)

    async def test_exception_and_excluded_paths(self):
        s=await self.run_request(crash=True);self.assertEqual(s['failed'],1);self.assertEqual(s['in_flight'],0)
        s=await self.run_request(path='/admin/api/overview');self.assertEqual(s['completed'],0)
        s=await self.run_request(source='test');self.assertEqual(s['test_count'],1);self.assertEqual(s['api_count'],0)

    async def test_privacy_and_bounded_history(self):
        s=await self.run_request(chunks=[b'{"secret":"do-not-store"}'])
        self.assertNotIn('do-not-store',json.dumps(s))
        metrics=RequestMetrics()
        for _ in range(110):
            metrics.begin();metrics.finish('/v1/messages','api',200,True,10,'success')
        s=metrics.snapshot();self.assertEqual(len(s['recent']),100);self.assertEqual(s['completed'],110);self.assertEqual(s['avg_duration_ms'],10)

    async def test_client_disconnect(self):
        metrics=RequestMetrics()
        async def app(scope,receive,send):
            await send({'type':'http.response.start','status':200,'headers':[(b'content-type',b'text/event-stream')]})
            await receive()
        async def receive():return {'type':'http.disconnect'}
        async def send(m):pass
        await MetricsMiddleware(app,metrics)({'type':'http','method':'POST','path':'/v1/messages'},receive,send)
        s=metrics.snapshot();self.assertEqual(s['failed'],1);self.assertEqual(s['recent'][0]['outcome'],'interrupted')


if __name__=='__main__':unittest.main()
