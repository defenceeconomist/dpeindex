"""Scheduling boundaries: no-op behavior, publication gates, and durable retries."""
import importlib.util
import json
from pathlib import Path
import tempfile
import fcntl
import unittest
from unittest.mock import patch

from app import scheduled_update as worker

spec = importlib.util.spec_from_file_location('weekly', Path(__file__).resolve().parents[1] / 'scripts/weekly-update.py')
weekly = importlib.util.module_from_spec(spec)
spec.loader.exec_module(weekly)


class ScheduledTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.state = Path(self.temp.name)
        self.config = {'root': str(self.state), 'python': 'python', 'node': 'node', 'gh': 'gh'}

    def test_unchanged_never_prepares_updates(self):
        with patch.object(worker, 'update_slot'), patch.object(worker, 'current_release', return_value=self.state), \
             patch.object(worker, 'verify_release'), patch.object(worker, 'fetch_snapshot', return_value={
                 'changes': {'added': 0, 'changed': 0, 'removed': 0}}), patch.object(worker, 'prepare') as prepare:
            worker.check(self.state)
        prepare.assert_not_called()
        self.assertEqual(weekly.read(self.state / 'check.json')['status'], 'unchanged')
        self.assertFalse((self.state / 'pending.json').exists())

    def test_removals_stop_before_prepare(self):
        with patch.object(worker, 'update_slot'), patch.object(worker, 'current_release', return_value=self.state), \
             patch.object(worker, 'verify_release'), patch.object(worker, 'fetch_snapshot', return_value={
                 'changes': {'added': 0, 'changed': 0, 'removed': 1}}), patch.object(worker, 'prepare') as prepare:
            with self.assertRaisesRegex(ValueError, 'removed'):
                worker.check(self.state)
        prepare.assert_not_called()

    def test_changed_records_prepare_checked_refresh(self):
        previous = self.state / 'previous/release'
        previous.mkdir(parents=True)
        weekly.save(previous.parent / 'state.json', {k: str(self.state / k) for k in ('topic_cache', 'citation_cache', 'search_cache')})
        source = self.state / 'source.redif'
        weekly.save(self.state / 'manifest.json', {'sha256': 'test'})
        snapshot = {'changes': {'added': 1, 'changed': 1, 'removed': 0}, 'source': str(source)}
        with patch.object(worker, 'update_slot'), patch.object(worker, 'current_release', return_value=previous), \
             patch.object(worker, 'verify_release'), patch.object(worker, 'fetch_snapshot', return_value=snapshot), \
             patch.object(worker, 'prepare') as prepare:
            worker.check(self.state)
        arguments = prepare.call_args.args[0]
        self.assertFalse(arguments.skip_browser_tests)
        self.assertFalse(arguments.without_citations)
        self.assertFalse(arguments.allow_removals)
        self.assertTrue((self.state / 'pending.json').exists())

    def test_noop_supervisor_never_publishes(self):
        weekly.save(self.state / 'check.json', {'status': 'unchanged'})
        def fake_command(*args, **kwargs):
            weekly.save(self.state / 'check.json', {'status': 'unchanged'})
        with patch.object(weekly, 'command', side_effect=fake_command), patch.object(weekly, 'publish') as publish:
            self.assertEqual(weekly.execute(self.config, self.state)['status'], 'unchanged')
        publish.assert_not_called()

    def test_pending_publication_retries_without_fetching(self):
        pending = {'workspace': str(self.state / 'workspace')}
        weekly.save(self.state / 'pending.json', pending)
        with patch.object(weekly, 'command') as command, patch.object(weekly, 'publish', return_value=pending):
            self.assertEqual(weekly.execute(self.config, self.state)['status'], 'published')
        self.assertEqual(command.call_count, 1)
        self.assertIn('resume', command.call_args.args[0])

    def test_publication_failure_keeps_pending(self):
        pending = {'workspace': str(self.state / 'workspace')}
        weekly.save(self.state / 'pending.json', pending)
        with patch.object(weekly, 'command'), patch.object(weekly, 'publish', side_effect=RuntimeError('push failed')):
            with self.assertRaisesRegex(RuntimeError, 'push failed'):
                weekly.execute(self.config, self.state)
        self.assertEqual(weekly.read(self.state / 'pending.json'), pending)

    def test_worker_failure_prevents_publication(self):
        weekly.save(self.state / 'pending.json', {'workspace': str(self.state)})
        with patch.object(weekly, 'command', side_effect=RuntimeError('citation review required')), \
             patch.object(weekly, 'publish') as publish:
            with self.assertRaises(RuntimeError):
                weekly.execute(self.config, self.state)
        publish.assert_not_called()

    def test_browser_gate_rejects_failures_skips_and_flakes(self):
        for stats in ({'expected': 12, 'unexpected': 1}, {'expected': 11, 'skipped': 1},
                      {'expected': 12, 'flaky': 1}, {'expected': 0}):
            with self.subTest(stats=stats), patch.object(worker, 'verify_release'), patch.object(worker, 'read', side_effect=[
                {'finished': True, 'citations': True, 'browser_tests': True}, {'stats': stats}]):
                with self.assertRaisesRegex(ValueError, 'Browser'):
                    worker.verify(self.state)

    def test_email_failure_retains_outbox(self):
        weekly.queue_email(self.state, 'Update failed', 'Details')
        with patch.object(weekly, 'send_email', side_effect=OSError('offline')), patch.object(weekly.time, 'sleep'):
            with self.assertRaisesRegex(RuntimeError, 'retained'):
                weekly.flush_email(self.state)
        self.assertEqual(len(weekly.read(self.state / 'outbox.json')), 1)
        with patch.object(weekly, 'send_email'):
            weekly.flush_email(self.state)
        self.assertEqual(weekly.read(self.state / 'outbox.json'), [])

    def test_missing_disk_reports_error(self):
        self.config['root'] = str(self.state / 'missing')
        with self.assertRaisesRegex(RuntimeError, 'external SSD'):
            weekly.execute(self.config, self.state)

    def test_overlap_skips_without_starting_work(self):
        weekly.save(self.state / 'config.json', {'path': '/usr/bin:/bin'})
        with (self.state / 'run.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            with patch.object(weekly, 'SUPPORT', self.state), patch.object(weekly, 'LOGS', self.state), \
                 patch('sys.argv', ['weekly-update.py']), patch.object(weekly.os, 'umask'), \
                 patch.dict(weekly.os.environ), patch.object(weekly, 'execute') as execute:
                self.assertEqual(weekly.main(), 0)
            execute.assert_not_called()

    def test_success_recorded_only_after_exact_remote_deployment(self):
        release = self.state / 'workspace/release'
        weekly.save(release / 'site/release.json', {'release_id': 'release123'})
        weekly.save(release / 'update-report.json', {'changes': {'added': ['a'], 'changed': ['b'], 'removed': []}})
        pending = {'workspace': str(release.parent)}
        with patch.object(weekly, 'command', side_effect=[None, None, 'commit123', '{"release_id":"release123"}']), \
             patch.object(weekly, 'confirm_pages') as confirm:
            result = weekly.publish_locked(self.config, self.state, pending)
        confirm.assert_called_once_with(self.config, 'commit123')
        self.assertTrue(result['deployed'])
        self.assertEqual(result['changes'], {'added': 1, 'changed': 1, 'removed': 0})
        self.assertEqual(weekly.read(self.state / 'pending.json')['commit'], 'commit123')

    def test_stale_pending_release_cannot_overwrite_manual_update(self):
        with patch.object(worker, 'verify_release'), patch.object(worker, 'read', side_effect=[
            {'finished': True, 'citations': True, 'browser_tests': True}, {'stats': {'expected': 12}}]), \
             patch.object(worker, 'current_release', return_value=Path('/newer')):
            with self.assertRaisesRegex(ValueError, 'older pending release'):
                worker.verify(self.state)

    def test_deployment_failure_is_not_success(self):
        with patch.object(weekly, 'command', return_value=json.dumps({'commit': 'abc', 'status': 'errored', 'error': 'build failed'})):
            with self.assertRaisesRegex(RuntimeError, 'deployment failed'):
                weekly.confirm_pages(self.config, 'abc')

    def test_intervening_manual_update_blocks_resume(self):
        weekly.save(self.state / 'pending.json', {'workspace': str(self.state)})
        weekly.save(self.state / 'state.json', {'previous': '/old'})
        with patch.object(worker, 'update_slot'), patch.object(worker, 'current_release', return_value=Path('/new')), \
             patch.object(worker, 'run') as run:
            with self.assertRaisesRegex(RuntimeError, 'Current release changed'):
                worker.resume(self.state)
        run.assert_not_called()


if __name__ == '__main__':
    unittest.main()
