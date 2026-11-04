"""DC-fixed mAG wire bytes. No remote schema loading or replacement packs."""
import hashlib
import json
from pathlib import Path
from ._contract_schema import validate
from .task_v2_contract import decode_json_object

PREFIX = '/api/agent/activity-map/v1'
ORIGIN = 'https://kari.mayim-mayim.com'
SCHEMA_SHA256 = '866414b51d6dcb976261bbdbd133e016a6805325f7b8d518f61a149751229a68'

def wire(kind, value):
    raw = (Path(__file__).parent / 'contracts/mag-activity-map/mag-activity-map.v1.schema.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != SCHEMA_SHA256:
        raise ValueError('Invalid fixed Activity Map contract.')
    root = json.loads(raw)
    validate(root['$defs'][kind], value, root=root)
    return value

def decode(kind, raw):
    return wire(kind, decode_json_object(raw, 16 * 1024 * 1024))
