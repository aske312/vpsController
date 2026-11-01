"""Bound request buffering before JSON parsing and endpoint dependencies."""
from starlette.responses import JSONResponse

MAX_REQUEST_BYTES = 4 * 1024 * 1024


class RequestBodyLimit:
    def __init__(self, app, *, max_bytes=MAX_REQUEST_BYTES):
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope, receive, send):
        if scope['type'] != 'http':
            return await self.app(scope, receive, send)
        lengths = [value for key, value in scope.get('headers', []) if key.lower() == b'content-length']
        if lengths and (len(lengths) != 1 or not lengths[0].isdigit()):
            return await JSONResponse({'detail': 'Некорректный размер запроса'}, status_code=400)(scope, receive, send)
        if lengths and (len(lengths[0]) > 16 or int(lengths[0]) > self.max_bytes):
            return await self.reject(scope, receive, send)
        body = bytearray()
        while True:
            message = await receive()
            if message['type'] == 'http.disconnect':
                return
            chunk = message.get('body', b'')
            if len(body) + len(chunk) > self.max_bytes:
                return await self.reject(scope, receive, send)
            body.extend(chunk)
            if not message.get('more_body', False):
                break

        delivered = False

        async def bounded_receive():
            nonlocal delivered
            if not delivered:
                delivered = True
                return {'type': 'http.request', 'body': bytes(body), 'more_body': False}
            return await receive()

        await self.app(scope, bounded_receive, send)

    async def reject(self, scope, receive, send):
        await JSONResponse({'detail': 'Превышен допустимый размер запроса'}, status_code=413)(scope, receive, send)
