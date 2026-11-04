"""Finite, durable Activity Map acquisition. Recovery never initiates payment."""
from __future__ import annotations

import asyncio
import codecs
import hashlib
import json
import os
from pathlib import Path
import random
import re
import secrets
import stat
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from contextvars import ContextVar
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from functools import wraps, partial

import httpx

from . import activity_map_contract as c
from ._private_file_io import fsync_directory, read_bounded, write_all
from .immediate_visit_journal import _read, _write
from .activity_map_journal import journal_bytes
from .task_journal import _StableLock, _validate_journal_path
from .task_v2_contract import decode_json_object

_MAX_CONTROL = 2 * 1024 * 1024
_MAX_CATALOG = 16 * 1024 * 1024
_TERMINAL = {'COMPLETE', 'UNAVAILABLE', 'EXPIRED_UNCONFIRMED'}
_RECOVERY_CONTEXT = ContextVar('activity_map_recovery', default=None)


class ActivityMapError(Exception):
    """Safe public error; raw responses and recovery credentials never escape."""
    def __init__(self, code='ACQUISITION_UNCONFIRMED', status_code=None, recovery=None):
        self.code, self.status_code = code, status_code
        self.recovery = recovery
        super().__init__(json.dumps({'error':code, 'recovery':recovery}) if recovery else code)


def boundary(fn):
    @wraps(fn)
    def invoke(*args, **kwargs):
        error = None
        token = _RECOVERY_CONTEXT.set(None)
        try:
            return fn(*args, **kwargs)
        except ActivityMapError as exc:
            error = ActivityMapError(exc.code, exc.status_code, _RECOVERY_CONTEXT.get())
        except Exception:
            error = ActivityMapError(recovery=_RECOVERY_CONTEXT.get())
        finally:
            _RECOVERY_CONTEXT.reset(token)
        raise error
    return invoke


def _instant(value):
    d = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if d.tzinfo is None:
        raise ValueError('Invalid instant.')
    return d.timestamp()


def _hex(value, length):
    if not isinstance(value, str) or re.fullmatch('[a-f0-9]{%d}' % length, value) is None:
        raise ValueError('Invalid identity.')
    return value


def _private_directory(directory):
    directory = Path(os.path.abspath(str(directory)))
    directory.mkdir(mode=0o700, parents=True, exist_ok=True)
    _validate_journal_path(directory / 'probe')
    st = directory.lstat()
    if not stat.S_ISDIR(st.st_mode) or stat.S_ISLNK(st.st_mode) or getattr(st, 'st_file_attributes', 0) & 0x400:
        raise ValueError('Invalid private directory.')
    if os.name != 'nt' and (stat.S_IMODE(st.st_mode) != 0o700 or st.st_uid != os.geteuid()):
        raise ValueError('Private directory must be owned and mode 0700.')
    return directory


def _file_valid(path, descriptor):
    try:
        flags = os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0)
        fd = os.open(str(path), flags)
    except FileNotFoundError:
        return False
    try:
        st = os.fstat(fd)
        if not stat.S_ISREG(st.st_mode) or st.st_nlink != 1:
            raise ValueError('Invalid part file.')
        if st.st_size != descriptor['bytes']:
            return False
        h, size = hashlib.sha256(), 0
        decoder = codecs.getincrementaldecoder('utf-8')('strict')
        while True:
            chunk = os.read(fd, 65536)
            if not chunk:
                break
            size += len(chunk); h.update(chunk); decoder.decode(chunk)
            if size > descriptor['bytes']:
                return False
        decoder.decode(b'', final=True)
        return size == descriptor['bytes'] and h.hexdigest() == descriptor['sha256']
    except UnicodeError:
        return False
    finally:
        os.close(fd)


def _read_metadata(path, maximum):
    """Bound local metadata before allocation, including after a restart."""
    path = _validate_journal_path(path)
    fd = os.open(str(path), os.O_RDONLY | getattr(os, 'O_NOFOLLOW', 0))
    try:
        before = os.fstat(fd)
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1
                or before.st_size > maximum):
            raise ActivityMapError('METADATA_INVALID')
        raw = read_bounded(fd, maximum)
        after = os.fstat(fd)
        current = path.lstat()
        if (len(raw) > maximum or len(raw) != before.st_size
                or (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
                != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
                or (after.st_dev, after.st_ino) != (current.st_dev, current.st_ino)
                or not stat.S_ISREG(current.st_mode) or current.st_nlink != 1):
            raise ActivityMapError('METADATA_INVALID')
        return bytes(raw)
    finally:
        os.close(fd)


class ActivityMapClient:
    """An explicit execution capability; not instantiated by inspect MCP.

    ``payment_runtime`` is the caller's configured MagClient. Its existing
    policy, signers and budgets govern the one initial payment. A resumed
    journal only uses saved proof; it never calls a signer again.
    """
    def __init__(self, directory, *, payment_runtime=None, transport=None,
                 clock=time.time, sleep=time.sleep, jitter=random.random):
        self.directory = _private_directory(directory)
        self.payment_runtime = payment_runtime
        self.transport, self.clock, self.sleep, self.jitter = transport, clock, sleep, jitter

    def __repr__(self):
        return 'ActivityMapClient(<private>)'

    def _path(self, operation):
        return _validate_journal_path(self.directory / (_hex(operation, 32) + '.activity-map.json'))

    def _save(self, d):
        _write(self._path(d['operation_id']), d, maximum_bytes=_MAX_CONTROL, _encode=journal_bytes)
        # Only public handles, and only after the journal is durable. Separate
        # context per caller prevents concurrent operations sharing handles.
        _RECOVERY_CONTEXT.set({'operation_id':d['operation_id'],
                               'directory':str(self.directory)})

    def _load(self, operation):
        d = _read(self._path(operation), maximum_bytes=_MAX_CONTROL, _encode=journal_bytes)
        if d['schema_version'] != 'mag.activity-map.journal.v1' or d['operation_id'] != operation:
            raise ValueError('Journal binding mismatch.')
        _hex(d['recovery_secret'], 64)
        c.wire('purchase_request', d['request'])
        if d['request']['operation_id'] != operation or hashlib.sha256(d['request_bytes'].encode()).hexdigest() != d['request_sha256']:
            raise ValueError('Journal request mismatch.')
        if decode_json_object(d['request_bytes'].encode(), 32768) != d['request']:
            raise ValueError('Journal request mismatch.')
        _hex(d['generation'],32); _hex(d['manifest_sha256'],64)
        return d

    def _headers(self, d):
        # Retain old journals as evidence, but never replay retired Grant proof.
        identity = d.get('payment_identity') or {}
        if (d['request']['scheme'] == 'grant' or identity.get('rail') == 'grant'
                or identity.get('network') == 'offchain:grant'
                or identity.get('asset') == 'GRANT_CREDIT'):
            raise ActivityMapError('GRANT_NOT_SUPPORTED')
        return {'Authorization': 'MAG-Recovery ' + d['recovery_secret'], **d['payment_headers']}

    def _delay(self, header, attempt):
        delay = 2 ** attempt + self.jitter()
        if header:
            try:
                server_delay = float(header) if header.isdigit() else parsedate_to_datetime(header).timestamp() - self.clock()
                delay = max(delay, server_delay)
            except (ValueError, TypeError, OverflowError):
                pass
        return delay

    def _request(self, method, path, *, d=None, body=None, target=None, descriptor=None, expiry=True):
        # Paths are constructed exclusively here; no caller URL or redirects.
        if not path.startswith(c.PREFIX + '/'):
            raise ValueError('Invalid Activity Map path.')
        headers = {'Accept': 'application/json', 'Accept-Encoding': 'identity', 'User-Agent': 'mayim-agent/2.0.0'}
        if d is not None:
            headers.update(self._headers(d))
        if body is not None:
            headers['Content-Type'] = 'application/json'
        raw_body = json.dumps(body, separators=(',', ':'), ensure_ascii=False).encode() if body is not None else None
        for attempt in range(3):
            deadline = _instant(d['purchase']['expires_at']) if d and d.get('purchase') and d['purchase'].get('expires_at') else None
            if expiry and deadline is not None and self.clock() >= deadline:
                raise ActivityMapError('EXPIRED_UNCONFIRMED')
            temporary = None
            try:
                with httpx.Client(transport=self.transport, trust_env=False, follow_redirects=False,
                                  timeout=httpx.Timeout(60, connect=10), cookies=None) as client:
                    with client.stream(method, c.ORIGIN + path, headers=headers, content=raw_body) as response:
                        if response.status_code in (429, 503):
                            if attempt == 2:
                                raise ActivityMapError('RETRY_EXHAUSTED', response.status_code)
                            delay = self._delay(response.headers.get('retry-after'), attempt)
                        else:
                            if response.status_code not in (200, 202, 402, 409, 410):
                                raise ActivityMapError('HTTP_ERROR', response.status_code)
                            if target is not None and response.status_code == 200:
                                fd, temporary = tempfile.mkstemp(prefix='.part-', dir=str(target.parent))
                                h, count = hashlib.sha256(), 0
                                decoder = codecs.getincrementaldecoder('utf-8')('strict')
                                try:
                                    for chunk in response.iter_bytes(65536):
                                        count += len(chunk)
                                        if count > descriptor['bytes']:
                                            raise ActivityMapError('PART_INTEGRITY_ERROR')
                                        decoder.decode(chunk); h.update(chunk); write_all(fd, chunk)
                                    decoder.decode(b'', final=True)
                                    if count != descriptor['bytes'] or h.hexdigest() != descriptor['sha256']:
                                        raise ActivityMapError('PART_INTEGRITY_ERROR')
                                    os.fsync(fd)
                                finally:
                                    os.close(fd)
                                os.replace(temporary, target); temporary = None
                                fsync_directory(target.parent)
                                return 200, dict(response.headers), None
                            chunks, count = [], 0
                            for chunk in response.iter_bytes(65536):
                                count += len(chunk)
                                if count > _MAX_CONTROL:
                                    raise ActivityMapError('RESPONSE_TOO_LARGE')
                                chunks.append(chunk)
                            return response.status_code, dict(response.headers), b''.join(chunks)
            except (httpx.TimeoutException, httpx.TransportError):
                if attempt == 2:
                    raise ActivityMapError('RETRY_EXHAUSTED') from None
                delay = self._delay(None, attempt)
            finally:
                if temporary is not None:
                    os.unlink(temporary)
            if expiry and deadline is not None and self.clock() + delay >= deadline:
                raise ActivityMapError('EXPIRED_UNCONFIRMED')
            self.sleep(delay)
        raise ActivityMapError('RETRY_EXHAUSTED')

    def _adopt(self, d, raw, *, replacement=False):
        p = c.decode('purchase', raw)
        if p['operation_id'] != d['operation_id'] or p['purchase_id'] not in (None, d['operation_id']):
            raise ActivityMapError('PURCHASE_BINDING_ERROR')
        if p['generation'] != d['generation'] or p['manifest_sha256'] != d['manifest_sha256']:
            raise ActivityMapError('PURCHASE_BINDING_ERROR')
        old = d.get('purchase')
        if p['confirmed_at'] is not None:
            if not p['expires_at'] or _instant(p['expires_at']) != _instant(p['confirmed_at']) + 86400:
                raise ActivityMapError('PURCHASE_EXPIRY_ERROR')
            if old and old.get('confirmed_at') and any(old[k] != p[k] for k in ('confirmed_at', 'expires_at')):
                raise ActivityMapError('PURCHASE_EXPIRY_ERROR')
        elif p['state'] != 'PAYMENT_PENDING' or p['expires_at'] is not None:
            raise ActivityMapError('PURCHASE_BINDING_ERROR')
        d['purchase'] = p
        if p['state'] == 'COMPLETE' and d['complete_notification'] == 'sent_unconfirmed':
            d['complete_notification'] = 'confirmed'
        self._save(d)
        return p

    def _control(self, d, method, suffix='', body=None, *, expiry=True):
        status, _, raw = self._request(method, c.PREFIX + '/purchases/' + d['operation_id'] + suffix,
                                       d=d, body=body, expiry=expiry)
        if status != 200:
            value = c.decode('error', raw)
            if value.get('purchase'):
                self._adopt(d, json.dumps(value['purchase']).encode())
            raise ActivityMapError('CONTROL_UNCONFIRMED', status)
        return self._adopt(d, raw)

    def _result(self, d):
        p = d.get('purchase') or {}
        return {'operation_id': d['operation_id'], 'state': p.get('state', 'PAYMENT_PENDING'),
                'generation': d['generation'], 'confirmed_at': p.get('confirmed_at'),
                'expires_at': p.get('expires_at'), 'reason': p.get('reason'),
                'replacement': p.get('replacement'), 'actions': p.get('actions', ['recover']),
                'saved_directory': str(self.directory / d['operation_id'] / d['generation']),
                'verified_parts': d['verified_parts'], 'complete_notification': d['complete_notification']}

    @boundary
    def download(self, *, scheme, asset, agent_id=None):
        if scheme == 'grant' or asset == 'GRANT_CREDIT':
            raise ActivityMapError('GRANT_NOT_SUPPORTED')
        if agent_id is None and self.payment_runtime is not None:
            agent_id = getattr(self.payment_runtime, 'agent_id', None)
        status, _, raw = self._request('GET', c.PREFIX + '/manifest')
        if status != 200:
            raise ActivityMapError('MANIFEST_UNAVAILABLE', status)
        manifest = c.decode('manifest', raw)
        operation = secrets.token_hex(16)
        request = dict(operation_id=operation, generation=manifest['generation'],
                       manifest_sha256=hashlib.sha256(raw).hexdigest(), period='30d', scheme=scheme, asset=asset)
        if agent_id is not None:
            request['agentId'] = agent_id
        c.wire('purchase_request', request)
        request_bytes = json.dumps(request, separators=(',', ':'), ensure_ascii=False)
        d = dict(schema_version='mag.activity-map.journal.v1', operation_id=operation,
                 recovery_secret=secrets.token_hex(32), request=request, request_bytes=request_bytes,
                 request_sha256=hashlib.sha256(request_bytes.encode()).hexdigest(),
                 generation=manifest['generation'], manifest_sha256=request['manifest_sha256'],
                 original_manifest={k:manifest[k] for k in ('window','cutoff','policy_id','schema_version')}, purchase=None, payment_started=False, payment_headers={},
                 payment_identity=None, verified_parts=0, complete_notification='not_sent', replacement_request=None)
        with _StableLock(Path(str(self._path(operation)) + '.lock')):
            self._save(d)  # Must succeed before the first purchase/payment request.
            status, headers, raw = self._request('POST', c.PREFIX + '/purchases', d=d, body=request)
            if status not in (200, 402):
                raise ActivityMapError('PURCHASE_UNCONFIRMED', status)
            self._adopt(d, raw)
            if status == 402 and self.payment_runtime is not None:
                from .activity_map_payment import pay_once
                pay_once(self, d, headers, raw)
            if d['purchase']['state'] == 'ACQUIRING':
                self._acquire(d)
            return self._result(d)

    @boundary
    def status(self, operation_id):
        with _StableLock(Path(str(self._path(operation_id)) + '.lock')):
            d = self._load(operation_id)
            self._control(d, 'GET', expiry=False)
            return self._result(d)

    @boundary
    def resume(self, operation_id):
        with _StableLock(Path(str(self._path(operation_id)) + '.lock')):
            d = self._load(operation_id)
            status, _, raw = self._request('POST', c.PREFIX + '/purchases/recover', d=d,
                body={'operation_id':operation_id}, expiry=False)
            if status not in (200, 202):
                raise ActivityMapError('RECOVERY_UNCONFIRMED', status)
            if d['replacement_request'] is not None:
                return self._replace(d)
            self._adopt(d, raw)
            if d['purchase']['state'] == 'ACQUIRING':
                # A prior send does not establish either server completion or
                # continued integrity of files on disk after a restart.
                self._acquire(d)
            return self._result(d)

    def _part(self, d, directory, descriptor):
        c.wire('part', descriptor)
        target = _validate_journal_path(directory / descriptor['part_id'])
        if not _file_valid(target, descriptor):
            status, _, _ = self._request('GET', c.PREFIX + '/purchases/' + d['operation_id'] + '/parts/' + descriptor['part_id'],
                d=d, target=target, descriptor=descriptor)
            if status != 200:
                raise ActivityMapError('PART_UNAVAILABLE', status)
        return target

    def _acquire(self, d):
        directory = _private_directory(self.directory / d['operation_id'] / d['generation'])
        target = _validate_journal_path(directory / 'manifest.json')
        raw = _read_metadata(target, _MAX_CONTROL) if target.exists() else None
        cached = raw is not None and hashlib.sha256(raw).hexdigest() == d['manifest_sha256']
        if not cached:
            status, _, raw = self._request('GET', c.PREFIX + '/purchases/' + d['operation_id'] + '/manifest', d=d)
            if status != 200:
                raise ActivityMapError('MANIFEST_UNAVAILABLE', status)
        if hashlib.sha256(raw).hexdigest() != d['manifest_sha256']:
            raise ActivityMapError('MANIFEST_INTEGRITY_ERROR')
        manifest = c.decode('manifest', raw)
        if any(manifest[k] != v for k,v in d['original_manifest'].items()):
            raise ActivityMapError('REPLACEMENT_SCOPE_MISMATCH')
        if manifest['generation'] != d['generation']:
            raise ActivityMapError('MANIFEST_BINDING_ERROR')
        if not cached:
            fd, tmp = tempfile.mkstemp(dir=str(directory), prefix='.manifest-')
            try:
                write_all(fd,raw);os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp,target); fsync_directory(directory)
        # Catalogs are one level. Descriptor dedup also rejects ID collisions.
        seen = {}
        def unique(desc):
            c.wire('part',desc)
            if desc['part_id'] in seen:
                if seen[desc['part_id']] != desc:
                    raise ActivityMapError('PART_ID_CONFLICT')
                return False
            seen[desc['part_id']] = desc
            return True
        catalogs = []
        for desc in manifest['parts']:
            if desc['bytes'] > _MAX_CATALOG:
                raise ActivityMapError('CATALOG_TOO_LARGE')
            if unique(desc):
                catalogs.append(self._part(d,directory,desc))
        catalog_ids = set(seen)
        with ThreadPoolExecutor(max_workers=2) as pool:
            for path in catalogs:
                catalog = c.decode('part_catalog', _read_metadata(path, _MAX_CATALOG))
                batch = []
                for desc in catalog['parts']:
                    if desc['part_id'] in catalog_ids:
                        raise ActivityMapError('NESTED_CATALOG_REJECTED')
                    if unique(desc):
                        batch.append(desc)
                    if len(batch)==2:
                        list(pool.map(lambda x:self._part(d,directory,x),batch));batch=[]
                list(pool.map(lambda x:self._part(d,directory,x),batch))
                d['verified_parts']=len(seen);self._save(d)
        d['verified_parts']=len(seen);self._save(d)
        self._complete(d)

    def _complete(self,d):
        d['complete_notification']='sent_unconfirmed';self._save(d)
        self._control(d,'POST','/complete',dict(generation=d['generation'],manifest_sha256=d['manifest_sha256']))
        if d['purchase']['state']=='COMPLETE':
            d['complete_notification']='confirmed';self._save(d)

    @boundary
    def replacement(self, operation_id, *, decision, generation=None, manifest_sha256=None):
        with _StableLock(Path(str(self._path(operation_id)) + '.lock')):
            d=self._load(operation_id)
            self._control(d,'GET',expiry=False)
            if d['purchase']['state']!='REPLACEMENT_REQUIRED':
                return self._result(d)
            body=dict(generation=d['generation'],decision=decision)
            if decision=='accept':
                body.update(replacement_generation=_hex(generation,32),manifest_sha256=_hex(manifest_sha256,64))
            c.wire('replacement_request',body)
            d['replacement_request']=body;self._save(d)
            return self._replace(d)

    def _replace(self,d):
        body=d['replacement_request']
        status,_,raw=self._request('POST',c.PREFIX+'/purchases/'+d['operation_id']+'/replacement',d=d,body=body)
        if status!=200:
            raise ActivityMapError('REPLACEMENT_UNCONFIRMED',status)
        if body['decision']=='accept':
            d['generation']=body['replacement_generation'];d['manifest_sha256']=body['manifest_sha256']
            d['verified_parts']=0;d['complete_notification']='not_sent'
        self._adopt(d,raw)
        d['replacement_request']=None;self._save(d)
        if d['purchase']['state']=='ACQUIRING':
            self._acquire(d)
        return self._result(d)

    async def download_async(self, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(None, partial(self.download, **kwargs))

    async def resume_async(self, operation_id):
        return await asyncio.get_running_loop().run_in_executor(None, self.resume, operation_id)

    async def status_async(self, operation_id):
        return await asyncio.get_running_loop().run_in_executor(None, self.status, operation_id)

    async def replacement_async(self, operation_id, **kwargs):
        return await asyncio.get_running_loop().run_in_executor(None, partial(self.replacement, operation_id, **kwargs))
