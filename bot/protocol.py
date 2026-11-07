"""Strict JSON Lines wire contract; no database/process dependencies."""
import json
import re
from .urls import validate_url
MAX_MESSAGE = 64 * 1024
CODES = {'480p', '720p', '1080p', 'MP3', 'video', 'audio'}
STATES = {'accepted', 'running', 'completed', 'failed', 'cancelled', 'not_found'}
class ProtocolError(ValueError):
    pass

def pairs(items):
    result = {}
    for k, v in items:
        if k in result:
            raise ProtocolError('duplicate_key')
        result[k] = v
    return result

def encode(value):
    raw = json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(',', ':')).encode() + b'\n'
    if len(raw) > MAX_MESSAGE:
        raise ProtocolError('message_too_large')
    return raw

def decode(raw):
    if not 0 < len(raw) <= MAX_MESSAGE or not raw.endswith(b'\n'):
        raise ProtocolError('invalid_frame')
    try:
        v = json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ProtocolError('nonfinite')))
    except (ValueError, UnicodeError):
        raise ProtocolError('invalid_json') from None
    if type(v) is not dict:
        raise ProtocolError('invalid_object')
    return v

def identity(v):
    if (type(v.get('version')) is not int or v['version'] != 1
            or type(v.get('task_id')) is not int or not 0 < v['task_id'] < 2**63
            or type(v.get('request_id')) is not str
            or re.fullmatch(r'[A-Za-z0-9_-]{1,64}', v['request_id']) is None):
        raise ProtocolError('invalid_identity')

def request(raw):
    v = decode(raw)
    if set(v) != {'version', 'request_id', 'task_id', 'action', 'payload'}:
        raise ProtocolError('unknown_fields')
    identity(v)
    a, p = v['action'], v['payload']
    if type(a) is not str or a not in {'inspect', 'download', 'cancel', 'status'} or type(p) is not dict:
        raise ProtocolError('invalid_action')
    fields = {'url', 'platform', 'resource_id'} if a == 'inspect' else {'url', 'platform', 'resource_id', 'format'}
    if a in {'cancel', 'status'}:
        fields = set()
    if set(p) != fields:
        raise ProtocolError('invalid_payload')
    if a in {'inspect', 'download'}:
        u = validate_url(p['url'])
        if (u.url, u.platform, u.resource_id) != (p['url'], p['platform'], p['resource_id']):
            raise ProtocolError('invalid_resource')
        if a == 'download' and (type(p['format']) is not str or p['format'] not in CODES):
            raise ProtocolError('invalid_format')
    return v

def response(raw, sent):
    v = decode(raw)
    if not {'version', 'request_id', 'task_id', 'status'} <= v.keys() or v.keys() - {'version', 'request_id', 'task_id', 'status', 'result', 'error'}:
        raise ProtocolError('invalid_response')
    identity(v)
    if v['request_id'] != sent['request_id'] or v['task_id'] != sent['task_id'] or type(v['status']) is not str or v['status'] not in STATES:
        raise ProtocolError('mismatched_response')
    if any(k in v and type(v[k]) is not dict for k in ('result','error')):
        raise ProtocolError('invalid_response_data')
    return v
