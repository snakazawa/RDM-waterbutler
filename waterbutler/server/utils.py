import tornado.iostream

from waterbutler.core.exceptions import InvalidHeaderError
from waterbutler.server import settings

CORS_ACCEPT_HEADERS = [
    'Range',
    'Content-Type',
    'Authorization',
    'Cache-Control',
    'X-Requested-With',
]

CORS_EXPOSE_HEADERS = [
    'Range',
    'Accept-Ranges',
    'Content-Range',
    'Content-Length',
    'Content-Encoding',
]

HTTP_REASONS = {
    422: 'Unprocessable Entity',
    461: 'Unavailable For Legal Reasons',
}


def make_disposition(filename):
    return 'attachment;filename="{}"'.format(filename.replace('"', '\\"'))


def parse_request_range(range_header):
    """WB uses tornado's httputil._parse_request_range function to parse the Range HTTP header and
    return a tuple representing the range.  Tornado's version returns a tuple suitable for slicing
    arrays, meaning that a range of 0-1 will be returned as ``(0, 2)``.  WB had been assuming that
    the tuple would represent the first and last byte positions and was consistenly returning one
    more byte than requested. Since WB doesn't ever use ranges to do list slicing of byte streams,
    this function wraps tornado's version and returns the actual byte indices.

    Ex. ``Range: bytes=0-1`` will be returned as ``(0, 1)``.

    If the end byte is omitted, the second element of the tuple will be ``None``. If no byte range
    is given or an unfamiliar formatting is used, this function will return ``None``.  This
    function does not understand multiple ranges (``Range: bytes:0-1,5-6``) and will return
    ``None`` if given one.

    If the start byte is omitted or if the end byte is less than the first byte an
    `InvalidHeaderError` will be raised.

    :param str range_header: a string containing the value of the Range header
    :rtype: `tuple` or `None`
    :return: a `tuple` representing the inclusive range of byte positions or `None`.
    """
    request_range = tornado.httputil._parse_request_range(range_header)

    if request_range is None:
        return request_range

    start, end = request_range
    if start is None and end is None:
        return None
    elif start is None or start < 0:
        raise InvalidHeaderError('Unsupported Range header format: {}'.format(range_header))

    if end is not None:
        end -= 1
        if end < start:
            raise InvalidHeaderError('Unsupported Range header format: {}'.format(range_header))

    return (start, end)


class CORsMixin:

    def _cross_origin_is_allowed(self):
        if self.request.method == 'OPTIONS':
            return True
        elif not self.request.cookies and self.request.headers.get('Authorization'):
            return True
        return False

    def set_default_headers(self):
        if not self.request.headers.get('Origin'):
            return

        allowed_origin = None
        if self._cross_origin_is_allowed():
            allowed_origin = self.request.headers['Origin']
        elif isinstance(settings.CORS_ALLOW_ORIGIN, str):
            if settings.CORS_ALLOW_ORIGIN == '*':
                # Wild cards cannot be used with allowCredentials.
                # Match Origin if its specified, makes pdfs and pdbs render properly
                allowed_origin = self.request.headers['Origin']
            else:
                allowed_origin = settings.CORS_ALLOW_ORIGIN
        else:
            if self.request.headers['Origin'] in settings.CORS_ALLOW_ORIGIN:
                allowed_origin = self.request.headers['Origin']

        if allowed_origin is not None:
            self.set_header('Access-Control-Allow-Origin', allowed_origin)

        self.set_header('Access-Control-Allow-Credentials', 'true')
        self.set_header('Access-Control-Allow-Headers', ', '.join(CORS_ACCEPT_HEADERS))
        self.set_header('Access-Control-Expose-Headers', ', '.join(CORS_EXPOSE_HEADERS))
        self.set_header('Cache-control', 'no-store, no-cache, must-revalidate, max-age=0')

    def options(self, *args, **kwargs):
        self.set_status(204)
        if self.request.headers.get('Origin'):
            self.set_header('Access-Control-Allow-Methods', 'GET, PUT, POST, DELETE'),


class UtilMixin:

    bytes_downloaded = 0
    bytes_uploaded = 0

    def set_status(self, code, reason=None):
        return super().set_status(code, reason or HTTP_REASONS.get(code))

    async def write_stream(self, stream):
        try:
            while True:
                chunk = await stream.read(settings.CHUNK_SIZE)
                if not chunk:
                    break
                # Temp fix, write does not accept bytearrays currently
                if isinstance(chunk, bytearray):
                    chunk = bytes(chunk)
                self.write(chunk)
                self.bytes_downloaded += len(chunk)
                del chunk
                await self.flush()
        except tornado.iostream.StreamClosedError:
            # Client has disconnected early.
            # No need for any exception to be raised
            return
