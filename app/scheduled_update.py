"""Checked weekly refresh worker, sharing the MCP update lease and saved caches."""
import json
from pathlib import Path
import sys

from .mcp_jobs import current_release, fetch_snapshot, new_id, update_slot
from .update import ARTIFACTS, ROOT, build_parser, prepare, read, run, verify_release


def save(path, value):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(value, indent=2) + '\n')
    temporary.replace(path)


def check(state_dir):
    with update_slot():
        previous = current_release()
        verify_release(previous)
        snapshot = fetch_snapshot()
        changes = snapshot['changes']
        if changes['removed']:
            raise ValueError('RePEc removed existing records; manual review required')
        if not changes['added'] and not changes['changed']:
            save(state_dir / 'check.json', {'status': 'unchanged', 'changes': changes})
            return
        workspace = ARTIFACTS / 'scheduled-updates' / new_id()
        argv = ['refresh', '--source', snapshot['source'], '--previous', str(previous),
                '--output', str(workspace), '--decisions', str(ROOT / 'data/citation-decisions.json')]
        prior = read(previous.parent / 'state.json')
        for key in ('topic_cache', 'citation_cache', 'search_cache'):
            argv.extend(['--' + key.replace('_', '-'), prior[key]])
        prepare(build_parser().parse_args(argv), provenance=read(Path(snapshot['source']).parent / 'manifest.json'))
        save(state_dir / 'pending.json', {'workspace': str(workspace)})
        save(state_dir / 'check.json', {'status': 'changed', 'changes': changes})


def resume(state_dir):
    with update_slot():
        workspace = Path(read(state_dir / 'pending.json')['workspace'])
        state = read(workspace / 'state.json')
        if state.get('finished'):
            verify_release(workspace / 'release')
            return
        if current_release() != Path(state['previous']):
            raise RuntimeError('Current release changed since preparation; review pending workspace before retry')
        run(state)


def verify(release):
    verify_release(release)
    state = read(release.parent / 'state.json')
    if not state.get('finished') or not state['citations'] or not state['browser_tests']:
        raise ValueError('Only complete, citation-enabled, browser-checked releases may be scheduled for publication')
    stats = read(release / 'browser-results.json')['stats']
    if stats.get('unexpected') or stats.get('skipped') or stats.get('flaky') or not stats.get('expected'):
        raise ValueError('Browser verification was not fully successful')
    if current_release() != release.resolve():
        raise ValueError('Current release changed; refusing to publish an older pending release')


if __name__ == '__main__':
    {'check': check, 'resume': resume, 'verify': verify}[sys.argv[1]](Path(sys.argv[2]))
