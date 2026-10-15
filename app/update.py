"""Local, resumable releases. No database, application server or containers."""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import time

from .citation_data import write_json, digest
from .topic_store import corpus_fingerprint
from .repec import SOURCE, fetch_redif, source_metadata, validate_redif

ROOT = Path(__file__).resolve().parents[1]
ARTIFACTS = ROOT / 'data/local-artifacts'
REVISION = '97b0c614be4d77ee51c0cef4e5f07c00f9eb65b3'


def read(path):
    return json.loads(Path(path).read_text())


def hashes(directory):
    return {str(p.relative_to(directory)): hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(Path(directory).rglob('*')) if p.is_file()}


def verify(directory, expected):
    for name, checksum in expected.items():
        path = Path(directory) / name
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != checksum:
            raise ValueError(f'Corrupt completed artifact: {path}')


def verify_release(directory):
    manifest = read(Path(directory) / 'release-manifest.json')
    verify(directory, manifest['files'])
    return manifest


def citation_decisions_match(previous, decisions):
    """Only reuse a release when its reviewed citation inputs also match."""
    manifest = read(previous / 'release-manifest.json')
    expected = manifest.get('citation_decisions_sha256')
    if expected is None:
        legacy = previous.parent / 'inputs/decisions.json'
        expected = digest(read(legacy)) if legacy.exists() else None
    return expected is not None and expected == digest(read(decisions))


@contextmanager
def lease(directory):
    directory.mkdir(parents=True, exist_ok=True)
    with (directory / '.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('Another update is using this workspace') from None
        try:
            yield
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def diff_articles(before, after):
    old, new = ({a['handle']: a for a in rows} for rows in (before, after))
    if len(old) != len(before) or len(new) != len(after):
        raise ValueError('Duplicate article handles')
    changed = sorted(h for h in old.keys() & new.keys() if old[h] != new[h])
    return {'added': sorted(new.keys() - old.keys()), 'removed': sorted(old.keys() - new.keys()),
            'changed': changed, 'unchanged': len(old.keys() & new.keys()) - len(changed)}


def select_corpus(articles, through=None, exclude=()):
    def key(value):
        import re
        volume, issue = value.split(':')
        return tuple(int(x) for x in re.findall(r'\d+', volume)), tuple(int(x) for x in re.findall(r'\d+', issue))
    return sorted([a for a in articles if (issue := a['volume'] + ':' + a['issue']) not in exclude
                   and (through is None or key(issue) <= key(through))], key=lambda a: a['handle'])


def command(args, log):
    with log.open('w') as output:
        subprocess.run(args, cwd=ROOT, stdout=output, stderr=subprocess.STDOUT, check=True)


def stage(state, name, action, outputs):
    directory = Path(state['directory'])
    if name in state['completed']:
        verify(directory, state['completed'][name]['files'])
        return
    started = time.monotonic()
    action()
    files = {}
    for output in outputs:
        path = directory / output
        if path.is_dir():
            files.update({str(path.relative_to(directory) / k): v for k, v in hashes(path).items()})
        else:
            files[output] = hashlib.sha256(path.read_bytes()).hexdigest()
    state['completed'][name] = {'files': files, 'seconds': time.monotonic() - started}
    write_json(directory / 'state.json', state)


def build_citations(directory, articles, topics, state):
    from .citation_data import collect, read_records
    from .citations import build_snapshot, exports
    from .update_topics import new_id
    decisions = read(directory / 'inputs/decisions.json')
    available = {a['handle'] for a in articles}
    full = read(directory / 'inputs/full-corpus.json')
    decisions['outside_catalogue_handles'] = sorted({a['handle'] for a in full} - available)
    # A source may refer to an intentionally withheld paper; preserve the review
    # evidence but omit that edge. Only audit sample edges present in this corpus.
    references = decisions.get('references', {})
    decisions.setdefault('audit', {})['doi_sample'] = [row for row in decisions.get('audit', {}).get('doi_sample', [])
        if row['reference_id'].rsplit('#', 1)[0] in available
        and references.get(row['reference_id'], {}).get('target', '') not in decisions['outside_catalogue_handles']]
    cache = Path(state['citation_cache'])
    records_path = directory / 'inputs/records.json'
    if not records_path.exists():
        collect(articles, cache, decisions, refresh=False, contact=os.getenv('CROSSREF_MAILTO', ''))
        write_json(records_path, read_records(articles, cache, decisions))
    records = read(records_path)
    if any(r['status'] in {'error', 'missing'} for r in records.values()):
        raise ValueError('Citation collection is incomplete; retry collection before release')
    if set(records) != available or any(r['sha256'] != digest(r.get('message')) for r in records.values()):
        raise ValueError('Citation input identities or hashes are inconsistent')
    snapshot, evidence, review = build_snapshot(articles, records, decisions, topics, new_id())
    target = directory / 'release/citations'
    target.mkdir(parents=True, exist_ok=True)
    write_json(directory / 'review.json', review)
    if snapshot['review']['pending_count'] or not snapshot['review']['doi_sample']:
        raise ValueError('Citation review required: inspect review.json, edit inputs/decisions.json, then resume')
    files = exports(snapshot, evidence, review, {'corpus': articles, 'records': records, 'topics': topics, 'decisions': decisions})
    snapshot['downloads'] = {name: {'sha256': hashlib.sha256(payload).hexdigest(), 'bytes': len(payload)} for name, payload in files.items()}
    snapshot.update(stale=False, topic_changed=False)
    for name, payload in files.items():
        (target / name).write_bytes(payload)
    write_json(directory / 'release/citations.json', snapshot)


def run(state):
    from .export_static import export_dataset
    from .topic_timeline import build_timeline
    from .update_topics import train, incremental
    from .topic_store import validate_snapshot
    directory = Path(state['directory'])
    with lease(directory):
        verify(directory, state['inputs'])
        for completed in state['completed'].values():
            verify(directory, completed['files'])
        previous = Path(state['previous']) if state['previous'] else None
        if previous:
            verify_release(previous)
        articles = read(directory / 'inputs/corpus.json')
        release = directory / 'release'
        release.mkdir(exist_ok=True)
        if state.get('finished'):
            verify_release(release)
            print(json.dumps({'status': 'unchanged', 'release': str(release), 'encoded': 0}))
            return
        old = read(previous / 'corpus.json') if previous else []
        changes = diff_articles(old, articles)
        # Exact replay uses the verified prior release, with no model or site work.
        if (previous and not any(changes[k] for k in ('added', 'changed', 'removed'))
                and state['operation'] == 'refresh'
                and (not state['citations'] or citation_decisions_match(previous, directory / 'inputs/decisions.json'))
                and state['citations'] == (read(previous / 'citations.json').get('status') == 'ready')
                and (not state['browser_tests'] or (previous / 'browser-results.json').exists())):
            state.update(finished=True, reused_release=str(previous), encoded_count=0)
            shutil.rmtree(release)
            shutil.copytree(previous, release)
            write_json(directory / 'state.json', state)
            write_json(ARTIFACTS / 'current.json', {'release': str(release)})
            print(json.dumps({'status': 'unchanged', 'release': str(release), 'encoded': 0}))
            return
        def topics_stage():
            if state['device'] == 'auto':
                import torch
                state['device'] = 'mps' if torch.backends.mps.is_available() else 'cpu'
            if previous and state['operation'] == 'refresh':
                prior = read(previous / 'topics.json')
                prior['_articles'] = old
                shutil.copytree(previous / 'model', release / 'model', dirs_exist_ok=True)
                if not any(changes[k] for k in ('added', 'changed', 'removed')):
                    snapshot = read(previous / 'topics.json')
                    coverage = read(previous / 'coverage.json')
                    coverage.update(encoded_count=0, cache_hits=coverage['checked_abstracts'])
                else:
                    snapshot, coverage = incremental(articles, prior, release / 'model', Path(state['topic_cache']), state['device'])
            else:
                settings = {'model_revision': REVISION}
                labels = read(directory / 'inputs/labels.json') if (directory / 'inputs/labels.json').exists() else None
                snapshot, coverage = train(articles, settings, release / 'model', Path(state['topic_cache']), state['device'], label_seed=labels)
            snapshot.update(status='ready', stale=False)
            if not (previous and state['operation'] == 'refresh' and not any(changes[k] for k in ('added', 'changed', 'removed'))):
                snapshot['timeline'] = build_timeline(snapshot, articles)
            validate_snapshot(snapshot)
            write_json(release / 'topics.json', snapshot)
            write_json(release / 'coverage.json', coverage)
            write_json(release / 'corpus.json', articles)
            write_json(release / 'source.json', {**state.get('source', {'url': SOURCE}),
                       'sha256': state['source_sha256'], 'count': len(articles)})
        stage(state, 'topics', topics_stage, ['release/model', 'release/topics.json', 'release/coverage.json', 'release/corpus.json', 'release/source.json'])
        topics = read(release / 'topics.json')
        def citations_stage():
            if state['citations']:
                build_citations(directory, articles, topics, state)
            else:
                write_json(release / 'citations.json', {'status': 'not_built', 'message': 'Citation analysis was not included in this release.'})
        stage(state, 'citations', citations_stage, ['release/citations.json'] + (['release/citations', 'inputs/decisions.json', 'inputs/records.json'] if state['citations'] else []))
        def export():
            target = release / 'dataset'
            if target.exists(): shutil.rmtree(target)
            export_dataset(target, articles, topics, read(release / 'citations.json'), read(release / 'source.json'),
                           release / 'citations' if state['citations'] else None)
        stage(state, 'dataset', export, ['release/dataset'])
        stage(state, 'website', lambda: command(['node', 'scripts/build-static.mjs', '--dataset', str(release / 'dataset'),
              '--output', str(release / 'site'), '--cache', state['search_cache'], '--metrics', str(release / 'search-metrics.json')], directory / 'website.log'), ['release/site', 'release/search-metrics.json'])
        if state['browser_tests']:
            stage(state, 'browsers', lambda: command(['node', 'scripts/verify-update-site.mjs', str(release)], directory / 'browsers.log'), ['release/browser-results.json'])
        before = {d['handle']: d for d in read(previous / 'topics.json')['documents']} if previous else {}
        preserved = sum(all(d.get(k) == before[d['handle']].get(k) for k in ('topic', 'x', 'y'))
                        for d in topics['documents'] if d['handle'] in before and d['handle'] not in changes['changed'])
        report = {'changes': changes, 'article_count': len(articles), 'model_id': topics['metadata']['model_id'],
                  'preserved_assignments_and_positions': preserved, 'topic_encoding': read(release / 'coverage.json'),
                  'search_encoding': read(release / 'search-metrics.json'),
                  'new_assignments': [d for d in topics['documents'] if d['handle'] in changes['added']],
                  'stages': {k: v['seconds'] for k, v in state['completed'].items()}}
        write_json(release / 'update-report.json', report)
        write_json(release / 'release-manifest.json', {'corpus_fingerprint': corpus_fingerprint(articles), 'citation_decisions_sha256': digest(read(directory / 'inputs/decisions.json')) if state['citations'] else None, 'files': {k: v for k, v in hashes(release).items() if k != 'release-manifest.json'}})
        state['finished'] = True
        write_json(directory / 'state.json', state)
        write_json(ARTIFACTS / 'current.json', {'release': str(release)})
        print(json.dumps({'status': 'ready', 'release': str(release), 'articles': len(articles)}))


def build_parser():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='operation', required=True)
    for name in ('train', 'refresh'):
        p = commands.add_parser(name)
        p.add_argument('--source', type=Path, default=Path('data/defpea.redif'))
        p.add_argument('--fetch', action='store_true')
        p.add_argument('--output', type=Path, required=True)
        p.add_argument('--previous', type=Path, required=name == 'refresh')
        p.add_argument('--through')
        p.add_argument('--exclude', nargs='*', default=['37:5', '37:6'] if name == 'train' else [])
        p.add_argument('--allow-removals', action='store_true')
        p.add_argument('--device', default='auto', choices=['auto', 'cpu', 'mps', 'cuda'])
        p.add_argument('--topic-cache', type=Path, default=ARTIFACTS / 'topic-cache')
        p.add_argument('--citation-cache', type=Path, default=Path('data/citation-artifacts'))
        p.add_argument('--search-cache', type=Path, default=ARTIFACTS / 'search-cache')
        p.add_argument('--decisions', type=Path, default=Path('data/citation-decisions.json'))
        p.add_argument('--labels', type=Path, help='Reviewed topic labels bound to training corpus and model evidence')
        p.add_argument('--without-citations', action='store_true')
        p.add_argument('--skip-browser-tests', action='store_true')
    for name in ('resume', 'status'):
        p = commands.add_parser(name); p.add_argument('workspace', type=Path)
    return parser


def prepare(args, *, provenance=None):
    """Freeze and validate inputs without running the expensive update stages."""
    if args.labels and args.operation != 'train':
        raise ValueError('Use --labels with train; incremental updates inherit frozen labels')
    directory = args.output.resolve()
    if directory.exists():
        raise ValueError('Output already exists; use resume or choose a new workspace')
    previous = args.previous.resolve() if args.previous else None
    if previous: verify_release(previous)
    if args.fetch:
        raw, provenance = fetch_redif()
    else:
        raw = args.source.read_bytes()
    full = validate_redif(raw)
    if provenance and provenance['sha256'] != hashlib.sha256(raw).hexdigest():
        raise ValueError('Source checksum does not match download provenance')
    provenance = provenance or source_metadata(raw, full, file=args.source.name)
    articles = select_corpus(full, args.through, args.exclude)
    if not articles: raise ValueError('Empty source selection')
    changes = diff_articles(read(previous / 'corpus.json') if previous else [], articles)
    if changes['removed'] and not args.allow_removals:
        raise ValueError('Source removes articles; use --allow-removals after review')
    # Reserve atomically so two callers cannot initialise the same workspace.
    directory.mkdir(parents=True, exist_ok=False)
    (directory / 'inputs').mkdir()
    (directory / 'inputs/source.redif').write_bytes(raw)
    write_json(directory / 'inputs/full-corpus.json', full)
    write_json(directory / 'inputs/source.json', provenance)
    write_json(directory / 'inputs/corpus.json', articles)
    shutil.copyfile(args.decisions, directory / 'inputs/decisions.json')
    shutil.copyfile(args.decisions, directory / 'inputs/original-decisions.json')
    write_json(directory / 'inputs/split.json', {'excluded_issues': args.exclude, 'through': args.through,
        'included_handles': [a['handle'] for a in articles], 'withheld_handles': sorted({a['handle'] for a in full} - {a['handle'] for a in articles})})
    if args.labels:
        shutil.copyfile(args.labels, directory / 'inputs/labels.json')
    immutable = hashes(directory / 'inputs'); immutable.pop('decisions.json')
    state = {'directory': str(directory), 'operation': args.operation, 'previous': str(previous) if previous else None,
             'source_sha256': hashlib.sha256(raw).hexdigest(), 'source': provenance, 'device': args.device,
             'topic_cache': str(args.topic_cache.resolve()), 'citation_cache': str(args.citation_cache.resolve()),
             'search_cache': str(args.search_cache.resolve()), 'citations': not args.without_citations,
             'browser_tests': not args.skip_browser_tests, 'completed': {},
             'inputs': {'inputs/' + k: v for k, v in immutable.items()}}
    write_json(directory / 'state.json', state)
    return state


def main():
    args = build_parser().parse_args()
    if args.operation in ('resume', 'status'):
        state = read(args.workspace / 'state.json')
        if args.operation == 'status':
            print(json.dumps(state, indent=2)); return
    else:
        state = prepare(args)
    run(state)

if __name__ == '__main__':
    main()
