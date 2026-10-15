"""Persistent local MCP jobs. Workers survive client disconnects and server restarts."""
from __future__ import annotations

from contextlib import contextmanager
from datetime import datetime, timezone
import fcntl
import os
from pathlib import Path
import re
import subprocess
import sys
import threading
import traceback
import uuid

from .citation_data import now, write_json
from .repec import fetch_redif, validate_redif
from .store import catalog_for
from .update import ARTIFACTS, ROOT, build_parser, diff_articles, prepare, read, run, verify_release


def new_id():
    return datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ-') + uuid.uuid4().hex[:8]


def artifact_path(kind, identifier):
    if not re.fullmatch(r'\d{8}T\d{6}Z-[0-9a-f]{8}', identifier):
        raise ValueError('Invalid snapshot or workspace ID')
    root = (ARTIFACTS / kind).resolve()
    path = root / identifier
    if path.resolve().parent != root:
        raise ValueError('Artifact must remain inside its local directory')
    return path


def current_release():
    pointer = ARTIFACTS / 'current.json'
    if not pointer.exists():
        raise ValueError('No current model; first train a baseline with python -m app.update train')
    return Path(read(pointer)['release']).resolve()


def change_summary(before, after):
    changes = diff_articles(before, after)
    return {**{k: len(changes[k]) for k in ('added', 'changed', 'removed')},
            'unchanged': changes['unchanged'],
            'sample_handles': {k: changes[k][:20] for k in ('added', 'changed', 'removed')}}


def fetch_snapshot():
    raw, manifest = fetch_redif()
    articles = validate_redif(raw)
    identifier = new_id()
    path = artifact_path('repec', identifier)
    path.mkdir(parents=True, exist_ok=False)
    (path / 'source.redif').write_bytes(raw)
    write_json(path / 'manifest.json', manifest)
    result = {'snapshot_id': identifier, 'source': str(path / 'source.redif'), **manifest,
              'latest_volume': catalog_for(articles)['volumes'][0]}
    if (ARTIFACTS / 'current.json').exists():
        previous = current_release()
        result.update(previous_release=str(previous), changes=change_summary(read(previous / 'corpus.json'), articles))
    return result


@contextmanager
def update_slot():
    """Transfer this flock to the worker; closing the parent's FD keeps it held."""
    ARTIFACTS.mkdir(parents=True, exist_ok=True)
    with (ARTIFACTS / '.mcp-update.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('An MCP model update is already running; check its status') from None
        yield lock
        # No LOCK_UN: a spawned worker shares the lock until its own FD closes.


def launch(directory, lock):
    job = {'status': 'queued', 'started_at': now()}
    write_json(directory / 'job.json', job)
    try:
        with (directory / 'worker.log').open('a') as log:
            process = subprocess.Popen(
                [sys.executable, '-m', 'app.mcp_jobs', str(directory), str(lock.fileno())],
                cwd=ROOT, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True, pass_fds=(lock.fileno(),))
    except Exception as error:
        write_json(directory / 'job.json', {**job, 'status': 'failed', 'error': str(error), 'finished_at': now()})
        raise
    write_json(directory / 'process.json', {'pid': process.pid})
    # Reap workers while this server lives. Detached workers also outlive it.
    threading.Thread(target=process.wait, daemon=True).start()
    return {'workspace_id': directory.name, 'workspace': str(directory), 'status': 'started',
            'pid': process.pid, 'log': str(directory / 'worker.log'),
            'next_step': 'Call dpe_update_status with this workspace_id to check progress.'}


def update_model(snapshot_id, *, through=None, device='auto', without_citations=False, allow_removals=False):
    if device not in {'auto', 'cpu', 'mps', 'cuda'}:
        raise ValueError('Invalid device')
    if through is not None and not re.fullmatch(r'\d+:\d+(?:-\d+)?', through):
        raise ValueError('through must be a volume:issue, for example 37:6')
    snapshot = artifact_path('repec', snapshot_id)
    provenance = read(snapshot / 'manifest.json')
    with update_slot() as lock:
        previous = current_release()
        directory = artifact_path('mcp-updates', new_id())
        argv = ['refresh', '--source', str(snapshot / 'source.redif'), '--previous', str(previous),
                '--output', str(directory), '--device', device]
        # Reuse the current release's caches, including the pinned encoder cache.
        prior_state_path = previous.parent / 'state.json'
        if prior_state_path.exists():
            prior = read(prior_state_path)
            for key in ('topic_cache', 'citation_cache', 'search_cache'):
                argv.extend(['--' + key.replace('_', '-'), prior[key]])
        argv.extend(['--decisions', str(ROOT / 'data/citation-decisions.json')])
        if through: argv.extend(['--through', through])
        if without_citations: argv.append('--without-citations')
        if allow_removals: argv.append('--allow-removals')
        state = prepare(build_parser().parse_args(argv), provenance=provenance)
        result = launch(directory, lock)
        result['changes'] = change_summary(read(previous / 'corpus.json'), read(directory / 'inputs/corpus.json'))
        result['source_sha256'] = state['source_sha256']
        return result


def process_alive(pid):
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False


def update_status(workspace_id=None):
    if workspace_id is None:
        if not (ARTIFACTS / 'current.json').exists():
            return {'status': 'not_trained', 'message': 'Train a baseline with python -m app.update train'}
        release = current_release()
        topics = read(release / 'topics.json')
        return {'status': 'ready', 'release': str(release),
                'article_count': len(read(release / 'corpus.json')),
                'model_id': topics['metadata']['model_id'], 'source': read(release / 'source.json')}
    directory = artifact_path('mcp-updates', workspace_id)
    state = read(directory / 'state.json')
    job = read(directory / 'job.json') if (directory / 'job.json').exists() else {'status': 'interrupted'}
    process_file = directory / 'process.json'
    pid = job.get('pid') or (read(process_file)['pid'] if process_file.exists() else None)
    if job['status'] in {'queued', 'running'} and pid and not process_alive(pid):
        job = {**job, 'status': 'interrupted', 'error': 'Worker exited before recording a result; resume this workspace'}
    result = {**job, 'workspace_id': workspace_id, 'workspace': str(directory),
              'completed_stages': list(state['completed']), 'finished': state.get('finished', False),
              'log': str(directory / 'worker.log')}
    if state.get('finished'):
        result.update(status='ready', release=str(directory / 'release'),
                      reused_release=state.get('reused_release'))
        result['model_id'] = read(directory / 'release/topics.json')['metadata']['model_id']
        result['article_count'] = len(read(directory / 'release/corpus.json'))
        result['report'] = str(directory / 'release/update-report.json')
        if state.get('reused_release'):
            result['encoded_count'] = 0
    if (directory / 'review.json').exists():
        result['review'] = str(directory / 'review.json')
        result['decisions'] = str(directory / 'inputs/decisions.json')
    return result


def resume_update(workspace_id):
    directory = artifact_path('mcp-updates', workspace_id)
    with update_slot() as lock:
        state = read(directory / 'state.json')
        if state.get('finished'):
            verify_release(directory / 'release')
            return update_status(workspace_id)
        if Path(state['previous']) != current_release():
            raise ValueError('The current release has changed; fetch and start a fresh update')
        return launch(directory, lock)


def worker(directory, lock_fd):
    # The inherited descriptor must remain open for the entire worker lifetime.
    job = {'status': 'running', 'pid': os.getpid(), 'started_at': now()}
    write_json(directory / 'job.json', job)
    try:
        run(read(directory / 'state.json'))
    except Exception as error:
        traceback.print_exc()
        review_needed = str(error).startswith('Citation review required:')
        write_json(directory / 'job.json', {**job, 'status': 'review_required' if review_needed else 'failed',
                   'error': str(error), 'finished_at': now()})
        return 1
    else:
        write_json(directory / 'job.json', {**job, 'status': 'ready', 'finished_at': now()})
        return 0
    finally:
        os.close(lock_fd)


if __name__ == '__main__':
    sys.exit(worker(Path(sys.argv[1]), int(sys.argv[2])))
