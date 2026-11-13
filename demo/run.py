#!/usr/bin/env python3
"""Reproduce the bundled synthetic example through the existing MCP interfaces."""
from __future__ import annotations

import argparse
import asyncio
import csv
import fcntl
import hashlib
import html
import io
import json
import os
import sys
import uuid
from contextlib import AsyncExitStack
from decimal import Decimal
from pathlib import Path

HERE = Path(__file__).resolve().parent
CODEC = None


def encode(value):
    return CODEC.dumps(value) if CODEC else json.dumps(value, indent=2)


def decode(value):
    return CODEC.loads(value) if CODEC else json.loads(value)


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read(name):
    return (HERE / 'fixtures' / name).read_bytes().decode('utf-8')


def document(name):
    return decode(read(name))


def save(path, value):
    temporary = path.with_suffix('.tmp')
    with temporary.open('w', encoding='utf-8') as stream:
        os.chmod(temporary, 0o600)
        stream.write(encode(value))
        stream.write('\n')
    temporary.replace(path)


def unpack(result):
    if getattr(result, 'is_error', getattr(result, 'isError', False)):
        return {'_mcp_error': '\n'.join(getattr(c, 'text', '') for c in result.content)}
    text = '\n'.join(getattr(c, 'text', '') for c in result.content)
    try:
        return decode(text)
    except ValueError:
        value = getattr(result, 'structured_content', getattr(result, 'structuredContent', None))
        if isinstance(value, dict) and set(value) == {'result'}:
            return value['result']
        return value if value is not None else text


class Run:
    def __init__(self, session, output, metadata, mode, maximum):
        self.session, self.output, self.metadata = session, output, metadata
        self.path = output / 'resume.private.json'
        self.state = decode(self.path.read_text()) if self.path.exists() else {
            'product': metadata['product'], 'mode': mode, 'run_id': str(uuid.uuid4()),
            'max_units': maximum, 'started_units': 0, 'calls': {}, 'synthetic': True}
        assert self.state['product'] == metadata['product'] and self.state['mode'] == mode
        assert self.state['max_units'] == maximum, 'Resume with the original allowance'
        self.persist()

    def persist(self):
        save(self.path, self.state)

    async def call(self, label, name, args=None, *, repeat=False, error=False, units=0):
        args = args or {}
        identity = digest(encode([name, args]).encode())
        prior = self.state['calls'].get(label)
        if prior:
            assert prior['identity'] == identity, 'Saved request differs; use a new output directory for new work'
            if 'result' in prior and not repeat:
                return prior['result']
        else:
            assert self.state['started_units'] + units <= self.state['max_units'], 'Evaluation allowance exhausted'
            self.state['started_units'] += units
            self.state['calls'][label] = {'tool': name, 'identity': identity, 'arguments': args, 'reserved_units': units}
            self.persist()  # Preserve the original quote/key BEFORE an uncertain dispatch.
        print('MCP ' + name, file=sys.stderr, flush=True)
        result = unpack(await self.session.call_tool(name, args))
        if isinstance(result, dict) and '_mcp_error' in result:
            if not error:
                raise RuntimeError(name + ' failed; inspect private state and recover the original request')
        elif error:
            raise AssertionError(name + ' unexpectedly accepted the stop case')
        self.state['calls'][label]['result'] = result
        self.persist()
        return result

    async def job(self, label, start, get, quote, units=1):
        qid = quote['id']
        args = {'quote_id': qid, 'idempotency_key': 'demo-' + self.state['run_id'] + '-' + label}
        job = await self.call(label + '-start', start, args, units=1)
        for _ in range(90):
            current = await self.call(label + '-poll', get, {'job_id': job['id']}, repeat=True)
            if current.get('status') in {'completed', 'failed', 'cancelled'}:
                break
            await asyncio.sleep(1)
        else:
            raise RuntimeError('Still pending; resume the same output directory and allowance, never use a new key')
        assert current['status'] == 'completed', 'Job did not complete; review private state'
        receipt = current['receipt']
        consumed = next(receipt[k] for k in ('evaluations_consumed', 'checks_consumed', 'reports_consumed', 'units_consumed') if k in receipt)
        assert consumed == units, 'Unexpected consumption; stop and inspect the original receipt'
        reservation = self.state['calls'][label + '-start']
        if consumed == 0 and reservation.get('reserved_units') and not reservation.get('allowance_released'):
            self.state['started_units'] -= reservation['reserved_units']
            reservation['allowance_released'] = True
            self.persist()
        retry = await self.call(label + '-retry', start, args)
        assert retry['id'] == job['id'], 'Retry created another job'
        return current


async def feeds(run):
    p = document('profile.json')
    first = await run.call('first-upload', 'upload_supplier_feed', {'content': read('supplier.csv'), 'format': 'csv'})
    assert first['sha256'] == digest(read('supplier.csv').encode())
    await run.call('preview', 'preview_supplier_feed', {'upload_id': first['id'], 'target_schema': p['target_schema']})
    profile = await run.call('profile', 'create_supplier_feed_profile', {'upload_id': first['id'], **{k: p[k] for k in ('target_schema', 'mapping', 'contract')}})
    approved = await run.call('approve', 'approve_supplier_feed_profile', {'profile_id': profile['id'], 'expected_profile_hash': profile['hash'], 'approve': True})
    assert approved['approved']
    results = []
    for case, upload in [('first', first), ('later', None)]:
        if upload is None:
            upload = await run.call('later-upload', 'upload_supplier_feed', {'content': read('later.csv'), 'format': 'csv'})
        owned = await run.call(case + '-profile', 'get_supplier_feed_profile', {'profile_id': approved['id']})
        assert owned['approved'] and owned['hash'] == approved['hash']
        quote = await run.call(case + '-quote', 'quote_supplier_import', {'upload_id': upload['id'], 'profile_id': approved['id']})
        job = await run.job(case, 'start_supplier_import', 'get_supplier_import', quote)
        report = await run.call(case + '-report', 'get_supplier_import_report', {'job_id': job['id']})
        offset, text = 0, ''
        while True:
            page = await run.call(case + '-output-' + str(offset), 'get_supplier_import_output', {'job_id': job['id'], 'offset': offset})
            text += page['jsonl']
            if page['next_offset'] is None:
                assert page['complete']
                break
            offset = page['next_offset']
        receipt = job['receipt']
        assert receipt['verdict'] == 'passed' and receipt['evaluations_consumed'] == 1
        assert digest(text.encode()) == receipt['output_hash']
        rows = [json.loads(line) for line in text.splitlines()]
        assert len(rows) == 2 and rows[0]['sku'] == '000123' and rows[0]['price'] == '19.90'
        if case == 'later':
            assert rows[0]['stock'] == 13
        (run.output / (case + '-accepted.jsonl')).write_text(text)
        results.append({'case': case, 'report': report, 'rows': rows, 'output_sha256': digest(text.encode()), 'retry_same_job': True})
    drift = await run.call('drift-upload', 'upload_supplier_feed', {'content': read('drift.csv'), 'format': 'csv'})
    stop = await run.call('drift-quote', 'quote_supplier_import', {'upload_id': drift['id'], 'profile_id': approved['id']})
    assert stop.get('status') == 'review_required' or stop.get('verdict') == 'review_required', stop
    return {'deliveries': results, 'same_approved_profile': True, 'drift': stop, 'maximum_units': 2, 'destination': 'local preview only'}


async def quality(run, client):
    before = await run.call('wallet-before', 'quality_wallet')
    first = await run.call('continue-upload', 'upload_quality_file', {'content': read('continue.csv'), 'format': 'csv'})
    contract = await run.call('contract', 'draft_quality_contract', {'upload_id': first['id'], 'contract': document('contract.json')})
    approved = await run.call('approve', 'approve_quality_contract', {'contract_id': contract['id'], 'expected_hash': contract['hash'], 'approve': True})
    assert approved['approved']
    results = []
    for case, expected in [('continue', 'continue'), ('block', 'block'), ('warning', 'continue_with_warnings')]:
        raw = read(case + '.csv').encode()
        upload = first if case == 'continue' else await run.call(case + '-upload', 'upload_quality_file', {'content': raw.decode(), 'format': 'csv'})
        assert upload['sha256'] == digest(raw)
        quote = await run.call(case + '-quote', 'quote_quality_check', {'upload_id': upload['id'], 'contract_id': approved['id']})
        job = await run.job(case, 'start_quality_check', 'get_quality_check', quote)
        report = await run.call(case + '-report', 'get_quality_report', {'job_id': job['id']})
        assert report['verdict'] == expected, report
        assert job['receipt']['input_fingerprint'] == digest(raw)
        assert job['receipt']['checks_consumed'] == 1
        if case == 'continue':
            download = await run.call('original', 'get_checked_original_download', {'job_id': job['id']})
            assert download['input_fingerprint'] == digest(raw)
            path = download['download_path']
            assert path.startswith('/v1/quality/jobs/') and not path.startswith('//')
            response = await client.get('https://mcp.contentsweep.ai' + path)
            response.raise_for_status()
            assert response.content == raw
            (run.output / 'continue-original.csv').write_bytes(response.content)
            save(run.output / 'destination-preview.json', {'synthetic': True, 'write_performed': False,
                 'rows': list(csv.DictReader(io.StringIO(response.content.decode())))})
        else:
            await run.call(case + '-no-original', 'get_checked_original_download', {'job_id': job['id']}, error=True)
        results.append({'case': case, 'verdict': expected, 'input_sha256': digest(raw), 'report': report, 'retry_same_job': True, 'local_destination_preview': case == 'continue'})
    after = await run.call('wallet-after', 'quality_wallet')
    assert before['available_evaluations'] - after['available_evaluations'] == 3
    return {'cases': results, 'wallet_before': before, 'wallet_after': after, 'maximum_units': 3, 'original_bytes_verified': True}


async def inspector(run):
    before = await run.call('before', 'inspect_repository', {'path': 'defective'})
    after = await run.call('after', 'inspect_repository', {'path': 'corrected', 'baseline': before})
    assert before['metadata']['complete'] and after['metadata']['complete']
    assert len(before['findings']) == 10 and len(after['findings']) == 0
    assert len(after['metadata']['baseline']['resolved_instances']) == 10
    refused = await run.call('escape', 'inspect_repository', {'path': '../outside-root'})
    assert not refused['metadata']['complete'] and refused['metadata']['issues']
    items = ''.join('<li><strong>' + html.escape(f['id'] + ': ' + f['title']) + '</strong><p>'
                    + html.escape(str(f['evidence'])) + '</p></li>' for f in before['findings'])
    (run.output / 'handoff.html').write_text('<!doctype html><html lang="en"><meta charset="utf-8"><title>Synthetic release handoff</title><main><h1>FastAPI release handoff</h1><p>Owned synthetic fixture. Ten findings before, zero after; ten resolved instances. Static coverage only; no runtime certification, source upload or code execution.</p><h2>Resolved findings</h2><ul>' + items + '</ul></main></html>')
    return {'before': before, 'after': after, 'findings_before': 10, 'findings_after': 0, 'resolved': 10, 'root_escape_refused': True, 'source_uploaded': False, 'target_code_executed': False}


async def mapper(run):
    scope = {'user': 'synthetic-demo', 'vendor': 'synthetic-vendor', 'domain': 'customer-imports'}
    schema = await run.call('infer', 'infer_schema', {'source': str(HERE / 'fixtures/vendor.csv')})
    target = read('customer.schema.json')
    proposal = await run.call('propose', 'map_to_schema', {'source_schema_json': encode(schema), 'target_schema_json': target})
    await run.call('review', 'review_mapping', {'mapping_json': encode(proposal)})
    unresolved = await run.call('unresolved', 'transform_file', {'source': str(HERE / 'fixtures/vendor.csv'), 'mapping_json': encode(proposal), **scope}, error=True)
    resolved = await run.call('resolve', 'resolve_mapping', {'mapping_json': encode(proposal), 'decisions_json': read('decisions.json')})
    approved = await run.call('approve', 'approve_mapping', {'mapping_json': encode(resolved), 'expected_fingerprint': resolved['mapping_fingerprint'], 'reviewer': 'authorized synthetic-fixture reviewer', **scope})
    await run.call('validate', 'validate_schema_mapping', {'mapping_json': encode(approved), 'target_schema_json': target})
    value = await run.call('transform', 'transform_file', {'mapping_json': encode(approved), 'source': str(HERE / 'fixtures/vendor.csv'), **scope})
    assert value['records'][0]['customer_id'] == '000401'
    assert value['records'][0]['credit_limit'] == Decimal('123456789012.123456789'), 'Decimal precision lost'
    reuse = await run.call('reuse', 'transform_file', {'mapping_json': encode(approved), 'source': str(HERE / 'fixtures/later.csv'), **scope})
    drift = await run.call('drift', 'check_schema_drift', {'mapping_json': encode(approved), 'source': str(HERE / 'fixtures/drift.csv'), **scope})
    await run.call('drift-stop', 'transform_file', {'mapping_json': encode(approved), 'source': str(HERE / 'fixtures/drift.csv'), **scope}, error=True)
    code = await run.call('generate', 'generate_transformation', {'mapping_json': encode(approved)})
    if isinstance(code, dict):
        code = code['result']
    (run.output / 'transform.py').write_text(code)
    (run.output / 'transformed.json').write_text(encode(value['records']) + '\n')
    return {'unresolved': unresolved, 'approved_mapping': approved, 'transformed': value, 'reuse': reuse, 'drift': drift, 'exact_identifiers_and_decimals': True, 'wrapper_requires_matching_runtime': True}


async def evidence_product(run, product, local):
    dag = product == 'dag_doctor'
    cases = ['executor-mismatch-missing-context', 'executor-mismatch'] if dag else ['stateful-unsupported', 'blocked', 'supported']
    results = []
    wallet_name = 'dag_doctor_wallet' if dag else 'deployment_fixer_wallet'
    before = None if local else await run.call('wallet-before', wallet_name)
    for case in cases:
        bundle = document(case + '/bundle.json')
        if local:
            args = {'path': str(HERE / 'fixtures' / case / 'source')}
            if dag:
                args.update(runtime_context=bundle['runtime_context'], airflow_version=bundle['airflow_version'])
            report = await run.call(case, 'diagnose_airflow_dag' if dag else 'plan_deployment_readonly', args)
        else:
            args = {k: v for k, v in bundle.items() if k in ({'files', 'runtime_context', 'logs', 'airflow_version', 'suggest_patches'} if dag else {'files', 'region', 'image_uri', 'service_name'})}
            upload = await run.call(case + '-upload', 'upload_dag_bundle' if dag else 'upload_deployment_bundle', args)
            await run.call(case + '-preview', 'preview_dag_bundle' if dag else 'preview_deployment_bundle', {'upload_id': upload['id']})
            quote = await run.call(case + '-quote', 'quote_dag_diagnosis' if dag else 'quote_deployment_plan', {'upload_id': upload['id']})
            free = 'missing-context' in case or case == 'stateful-unsupported'
            job = await run.job(case, 'start_dag_diagnosis' if dag else 'start_deployment_plan', 'get_dag_diagnosis' if dag else 'get_deployment_plan', quote, units=0 if free else 1)
            report = await run.call(case + '-report', 'get_dag_report' if dag else 'read_deployment_report', {'job_id': job['id']})
            count = job['receipt']['reports_consumed' if dag else 'units_consumed']
            assert count == (0 if free else 1)
            results.append({'case': case, 'receipt': job.get('receipt'), 'report': report})
        if local:
            results.append({'case': case, 'report': report})
        if dag:
            assert ('DD-RUNTIME-006' in json.dumps(report)) if case == 'executor-mismatch' else report['verdict'] == 'needs_context'
        elif case == 'blocked':
            assert report['verdict'] == 'blocked'
        elif case == 'stateful-unsupported' and not local:
            assert report['verdict'] == 'unsupported'
    after = None if local else await run.call('wallet-after', wallet_name)
    if not local:
        assert before['available_evaluations'] - after['available_evaluations'] == (1 if dag else 2)
    return {'cases': results, 'wallet_before': before, 'wallet_after': after, 'cloud_actions': 0, 'target_code_executed': False, 'mode': 'local' if local else 'hosted'}


async def main(args, metadata):
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client
    from mcp.client.streamable_http import streamable_http_client
    try:
        import httpx2 as httpx
    except ImportError:
        import httpx
    output = args.output.resolve()
    if args.output.is_symlink() or HERE.is_relative_to(output):
        raise ValueError('Choose a dedicated output directory outside the demo source')
    if output.exists() and (output.is_symlink() or not output.is_dir()):
        raise ValueError('Output must be a private directory')
    output.mkdir(parents=True, exist_ok=True, mode=0o700)
    lock = (output / '.run.lock').open('a')
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError('Another run owns this output directory') from None
    async with AsyncExitStack() as stack:
        stack.callback(lock.close)
        client = None
        if args.mode == 'hosted':
            key = os.environ.get(metadata['setting'], '')
            if not key:
                raise ValueError('Set ' + metadata['setting'] + ' privately before hosted execution')
            if args.max_units is None or args.max_units < metadata['units']:
                raise ValueError('Explicit --max-units ' + str(metadata['units']) + ' or greater is required; see pricing')
            client = await stack.enter_async_context(httpx.AsyncClient(headers={'X-API-Key': key}, timeout=40))
            streams = await stack.enter_async_context(streamable_http_client(metadata['endpoint'], http_client=client))
        else:
            if not args.command or not Path(args.command).is_absolute() or not Path(args.command).is_file():
                raise ValueError('--command must be the installed MCP executable absolute path')
            command_args = ['--root', str(HERE / 'fixtures')] if metadata['product'] == 'inspector' else []
            streams = await stack.enter_async_context(stdio_client(StdioServerParameters(command=args.command, args=command_args)))
        session = await stack.enter_async_context(ClientSession(streams[0], streams[1]))
        initialized = await session.initialize()
        listed = await session.list_tools()
        run = Run(session, output, metadata, args.mode, args.max_units or 0)
        product = metadata['product']
        if args.mode == 'hosted':
            if product == 'feeds':
                # Supplier's public wallet is REST; its MCP contract has no wallet tool.
                wallet = run.state.get('wallet_preflight')
                if wallet is None:
                    response = await client.get('https://mcp.contentsweep.ai/v1/feeds/wallet')
                    response.raise_for_status()
                    wallet = response.json()
                    run.state['wallet_preflight'] = wallet
                    run.persist()
            else:
                wallet_tool = {'quality': 'quality_wallet', 'dag_doctor': 'dag_doctor_wallet',
                               'deployment_fixer': 'deployment_fixer_wallet'}[product]
                wallet = await run.call('wallet-preflight', wallet_tool)
            assert wallet['available_evaluations'] >= metadata['units'], 'Insufficient available allowance; stop without starting work'
        if product == 'feeds':
            result = await feeds(run)
        elif product == 'quality':
            result = await quality(run, client)
        elif product == 'inspector':
            result = await inspector(run)
        elif product == 'schema_mapper':
            result = await mapper(run)
        else:
            result = await evidence_product(run, product, args.mode == 'local')
        summary = {'synthetic': True, 'product': product, 'mode': args.mode, 'status': 'passed',
                   'server': (getattr(initialized, 'server_info', None) or initialized.serverInfo).model_dump(mode='json'),
                   'tools': [t.name for t in listed.tools], 'fixtures_sha256': metadata['fixtures_sha256'],
                   'result': result, 'execution': 'deterministic MCP client; not a native agent transcript'}
        save(output / 'result.json', summary)
        print(json.dumps({'status': 'passed', 'product': product, 'result': str(output / 'result.json')}))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--mode', choices=('hosted', 'local'))
    parser.add_argument('--command', help='Absolute path to an installed local MCP executable')
    parser.add_argument('--max-units', type=int, help='Explicit authorization for up to this many hosted evaluations')
    parser.add_argument('--output', type=Path, required=True, help='Private output/resume directory; reuse after uncertainty')
    args = parser.parse_args()
    metadata = json.loads((HERE / 'manifest.json').read_text())
    if metadata['product'] == 'schema_mapper':
        from mcp_products.schema_mapper import serialization as CODEC
    args.mode = args.mode or metadata['default_mode']
    if args.mode not in metadata['modes']:
        parser.error('This product does not support that mode')
    for filename, expected in metadata['fixtures_sha256'].items():
        if digest((HERE / 'fixtures' / filename).read_bytes()) != expected:
            parser.error('Fixture checksum mismatch: ' + filename)
    try:
        asyncio.run(main(args, metadata))
    except (ValueError, AssertionError, RuntimeError) as exc:
        parser.exit(2, str(exc) + '\n')
