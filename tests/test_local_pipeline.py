import copy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from app.citation_data import write_json
from app.redif import parse_file
from app.update import select_corpus, diff_articles, stage, verify_release, hashes, run
from app.export_static import export_dataset


class LocalPipelineTests(unittest.TestCase):
    def test_explicit_holdout_and_penultimate_arrival(self):
        full = parse_file(Path('data/defpea.redif'))
        training = select_corpus(full, exclude=['37:5', '37:6'])
        update = select_corpus(full, through='37:5')
        self.assertEqual((len(training), len(update)), (1207, 1214))
        self.assertEqual(sum(bool(a['abstract'].strip()) for a in training), 1138)
        self.assertEqual(len(diff_articles(training, update)['added']), 7)
        self.assertFalse(any(a['volume'] == '37' and a['issue'] in ['5', '6'] for a in training))
        self.assertTrue(any(a['issue'] == '1-2' for a in training))

    def test_stage_resume_checks_hash_and_failure_does_not_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp)
            state = {'directory': tmp, 'completed': {}}
            calls = []
            def action():
                calls.append(1); (directory / 'output').write_text('complete')
            stage(state, 'first', action, ['output'])
            stage(state, 'first', action, ['output'])
            self.assertEqual(len(calls), 1)
            (directory / 'output').write_text('corrupt')
            with self.assertRaisesRegex(ValueError, 'Corrupt'):
                stage(state, 'first', action, ['output'])
            with self.assertRaises(RuntimeError):
                stage(state, 'second', lambda: (_ for _ in ()).throw(RuntimeError('interrupted')), [])
            self.assertNotIn('second', state['completed'])

    def test_release_integrity_covers_model_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); (root / 'model').mkdir()
            (root / 'model/map.joblib').write_bytes(b'trusted local model')
            write_json(root / 'release-manifest.json', {'files': hashes(root)})
            verify_release(root)
            (root / 'model/map.joblib').write_bytes(b'corrupt')
            with self.assertRaisesRegex(ValueError, 'Corrupt'):
                verify_release(root)

    def test_metadata_changes_are_not_missed_by_update_diff(self):
        a = {'handle': 'a', 'abstract': 'same', 'keywords': 'old'}
        self.assertEqual(diff_articles([a], [{**a, 'keywords': 'new'}])['changed'], ['a'])

    def test_export_rejects_duplicate_records_before_writing(self):
        with tempfile.TemporaryDirectory() as tmp:
            a = parse_file(Path('data/defpea.redif'))[0]
            with self.assertRaisesRegex(ValueError, 'Duplicate'):
                export_dataset(Path(tmp), [a, a], {}, {}, {})
            self.assertEqual(list(Path(tmp).iterdir()), [])

    def test_removal_guard_leaves_source_and_previous_release_untouched(self):
        from app.update import main
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp); previous = root / 'previous'; previous.mkdir()
            write_json(previous / 'corpus.json', [{'handle': 'removed-record'}])
            before = Path('data/defpea.redif').read_bytes()
            argv = ['update', 'refresh', '--previous', str(previous), '--output', str(root / 'next'), '--device', 'cpu']
            with patch('sys.argv', argv), patch('app.update.verify_release'), self.assertRaisesRegex(ValueError, 'removes articles'):
                main()
            self.assertFalse((root / 'next').exists())
            self.assertEqual(Path('data/defpea.redif').read_bytes(), before)
            self.assertEqual(json.loads((previous / 'corpus.json').read_text()), [{'handle': 'removed-record'}])

    def test_citation_review_stops_before_dataset_site_or_current_publication(self):
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp); (directory / 'release').mkdir()
            write_json(directory / 'inputs/corpus.json', [])
            write_json(directory / 'release/topics.json', {})
            state = {'directory': tmp, 'previous': None, 'inputs': {}, 'completed': {}, 'citations': True}
            def skip_topics(state, name, action, outputs):
                if name != 'topics': action()
            with patch('app.update.stage', side_effect=skip_topics), patch('app.update.build_citations', side_effect=ValueError('review required')):
                with self.assertRaisesRegex(ValueError, 'review required'): run(state)
            self.assertFalse((directory / 'release/site').exists())
            self.assertFalse((directory / 'release/dataset').exists())
            self.assertNotIn('finished', state)

    def test_python_core_does_not_import_service_or_modelling_packages(self):
        import subprocess, sys
        result = subprocess.run([sys.executable, '-c',
            'import app.update, app.export_static, sys; '
            'assert not {"redis", "fastapi", "fastembed", "torch", "bertopic"}.intersection(sys.modules)'], capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr.decode())

if __name__ == '__main__': unittest.main()
