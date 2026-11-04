"""Graph journal checksum encoding, including finite remote JSON numbers.

This is private persistence, not a wire/signature JCS encoder. The previous
integer-only journal domain retains exactly its encoding and checksum. Graph
challenge numbers additionally round-trip as JSON numbers, without changing
the shared Task/Immediate JCS domain or the purchase request bytes/hash.
"""
import json

from .immediate_visit_profile import jcs_bytes


def journal_bytes(value):
    def encode(item):
        if type(item) is float:
            # Reject NaN/infinities, including JSON exponent overflow on read.
            return json.dumps(item, allow_nan=False)
        if type(item) in (list, tuple):
            return '[' + ','.join(encode(child) for child in item) + ']'
        if type(item) is dict:
            if any(type(key) is not str for key in item):
                raise ValueError('Invalid Graph journal value.')
            keys = sorted(item, key=lambda key: key.encode('utf-16-be'))
            return '{' + ','.join(encode(key) + ':' + encode(item[key]) for key in keys) + '}'
        return jcs_bytes(item).decode('utf-8')

    return encode(value).encode('utf-8')
