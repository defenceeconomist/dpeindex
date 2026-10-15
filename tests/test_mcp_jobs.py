import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app import mcp_jobs as jobs
from app.citation_data import write_json
from app.repec import source_metadata, validate_redif
from app.update import hashes, read
from test_repec import RAW


class MCPJobsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.patch = patch.object(jobs, 'ARTIFACTS', self.root)
        self.patch.start()
        self.addCleanup(self.patch.stop)
        self.identifier = '20261015T210000Z-0123abcd'

    def previous(self, articles):
        release = self.root / 'baseline/release'
        write_json(release / 'corpus.json', articles)
        write_json(release / 'release-manifest.json',
                   {'files': {k: v for k, v in hashes(release).items() if k != 'release-manifest.json'}})
        write_json(self.root / 'current.json', {'release': str(release)})
        return release

    def snapshot(self, raw=RAW):
        with patch.object(jobs, 'fetch_redif', return_value=(raw, source_metadata(raw, validate_redif(raw)))):
            return jobs.fetch_snapshot()

    def test_fetch_stages_a_snapshot_and_does_not_replace_current(self):
        self.previous([])
        pointer = (self.root / 'current.json').read_bytes()
        snapshot = self.snapshot()
        self.assertEqual(snapshot['changes']['added'], 1)
        self.assertEqual(Path(snapshot['source']).read_bytes(), RAW)
        self.assertEqual((self.root / 'current.json').read_bytes(), pointer)

    def test_snapshot_tampering_and_removals_never_launch(self):
        self.previous([])
        snapshot = self.snapshot()
        Path(snapshot['source']).write_bytes(RAW.replace(b'Test article', b'Changed article'))
        with patch.object(jobs, 'launch') as launch, self.assertRaisesRegex(ValueError, 'checksum'):
            jobs.update_model(snapshot['snapshot_id'])
        launch.assert_not_called()
        self.previous([{'handle': 'missing'}])
        snapshot = self.snapshot()
        with patch.object(jobs, 'launch') as launch, self.assertRaisesRegex(ValueError, 'removes articles'):
            jobs.update_model(snapshot['snapshot_id'])
        launch.assert_not_called()
        self.assertFalse((self.root / 'mcp-updates').exists())

    def test_update_freezes_inputs_and_preserves_browser_and_citation_checks(self):
        previous = self.previous([])
        snapshot = self.snapshot()
        with patch.object(jobs, 'launch', return_value={'status': 'started'}) as launch:
            result = jobs.update_model(snapshot['snapshot_id'], device='cpu')
        workspace = launch.call_args.args[0]
        state = read(workspace / 'state.json')
        self.assertEqual(state['previous'], str(previous))
        self.assertTrue(state['citations'])
        self.assertTrue(state['browser_tests'])
        self.assertEqual(state['operation'], 'refresh')
        self.assertEqual(result['changes']['added'], 1)
        self.assertIn('inputs/source.json', state['inputs'])
        self.assertFalse((workspace / 'release').exists())

    def test_invalid_ids_and_filters(self):
        for identifier in ['../current', '/tmp/a', 'abc', self.identifier + '/..']:
            with self.assertRaises(ValueError): jobs.artifact_path('repec', identifier)
        with self.assertRaisesRegex(ValueError, 'volume:issue'):
            jobs.update_model(self.identifier, through='garbage')

    def test_lock_is_held_by_child_after_parent_closes_descriptor(self):
        # A pipe synchronises the child, so the test needs no timing assumptions.
        read_fd, write_fd = os.pipe()
        try:
            with jobs.update_slot() as lock:
                process = subprocess.Popen([sys.executable, '-c', 'import os,sys; os.read(int(sys.argv[1]), 1)', str(read_fd)],
                                           pass_fds=(lock.fileno(), read_fd))
            with self.assertRaisesRegex(RuntimeError, 'already running'):
                with jobs.update_slot(): pass
            os.write(write_fd, b'x')
            process.wait(timeout=5)
            with jobs.update_slot(): pass
        finally:
            os.close(read_fd)
            os.close(write_fd)
            if 'process' in locals() and process.poll() is None:
                process.kill(); process.wait()

    def test_worker_failure_is_persistent_and_does_not_activate_release(self):
        directory = jobs.artifact_path('mcp-updates', self.identifier)
        write_json(directory / 'state.json', {'completed': {}})
        with jobs.update_slot() as lock, patch.object(jobs, 'run', side_effect=ValueError('Citation review required: inspect review.json')), \
                patch('app.mcp_jobs.traceback.print_exc'):
            result = jobs.worker(directory, os.dup(lock.fileno()))
        self.assertEqual(result, 1)
        self.assertEqual(jobs.update_status(self.identifier)['status'], 'review_required')
        self.assertFalse((self.root / 'current.json').exists())

    def test_stopped_worker_is_resumable_and_stale_parent_is_rejected(self):
        directory = jobs.artifact_path('mcp-updates', self.identifier)
        write_json(directory / 'state.json', {'completed': {}, 'previous': '/old/release'})
        write_json(directory / 'job.json', {'status': 'running', 'pid': 1})
        with patch.object(jobs, 'process_alive', return_value=False):
            self.assertEqual(jobs.update_status(self.identifier)['status'], 'interrupted')
            write_json(directory / 'job.json', {'status': 'queued'})
            write_json(directory / 'process.json', {'pid': 1})
            self.assertEqual(jobs.update_status(self.identifier)['status'], 'interrupted')
        self.previous([])
        with patch.object(jobs, 'launch') as launch, self.assertRaisesRegex(ValueError, 'current release has changed'):
            jobs.resume_update(self.identifier)
        launch.assert_not_called()

    def test_unchanged_worker_reuses_verified_release_without_encoding(self):
        previous = self.previous(validate_redif(RAW))
        write_json(previous / 'topics.json', {'metadata': {'model_id': 'frozen'}})
        write_json(previous / 'citations.json', {'status': 'not_built'})
        write_json(previous / 'browser-results.json', {'stats': {'unexpected': 0}})
        write_json(previous / 'release-manifest.json',
                   {'files': {k: v for k, v in hashes(previous).items() if k != 'release-manifest.json'}})
        snapshot = self.snapshot()
        with patch.object(jobs, 'launch', return_value={}) as launch:
            jobs.update_model(snapshot['snapshot_id'], without_citations=True)
        directory = launch.call_args.args[0]
        with jobs.update_slot() as lock, patch('app.update.ARTIFACTS', self.root), \
                patch('app.update_topics.incremental') as incremental, \
                patch('app.update_topics.train') as train, patch('builtins.print'):
            self.assertEqual(jobs.worker(directory, os.dup(lock.fileno())), 0)
        incremental.assert_not_called()
        train.assert_not_called()
        state = read(directory / 'state.json')
        self.assertTrue(state['finished'])
        self.assertEqual(state['encoded_count'], 0)
        self.assertEqual(state['reused_release'], str(previous))
        self.assertEqual(jobs.current_release(), directory / 'release')
        with patch.object(jobs, 'launch') as launch:
            self.assertEqual(jobs.resume_update(directory.name)['encoded_count'], 0)
        launch.assert_not_called()


if __name__ == '__main__':
    unittest.main()
