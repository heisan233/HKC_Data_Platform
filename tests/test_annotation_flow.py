"""QC entry guard and a complete Episode annotation flow."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException

import annotation_store
import dataset_store
from routers import annotation, qc


class AnnotationFlowTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        (self.root / 'meta').mkdir()
        (self.root / 'meta/info.json').write_text(json.dumps({'total_episodes': 2}))
        self.dataset = {'id': 'DS000001', 'name': '测试集', 'status': 'QC_PASS',
                        'available': True, 'raw_relpath': 'DS000001'}
        self.patches = [
            patch.object(dataset_store, 'DB', self.root / 'platform.sqlite'),
            patch.object(annotation, 'QC_DB', self.root / 'missing-qc.sqlite'),
            patch.object(annotation, 'get_dataset', lambda dataset_id: self.dataset),
            patch.object(annotation, 'dataset_root', lambda item: self.root),
            patch.object(qc, 'jobs', lambda: []),
            patch.object(qc, 'worker', self.worker),
        ]
        for item in self.patches:
            item.start()
        annotation_store.init_store()

    def tearDown(self):
        for item in reversed(self.patches):
            item.stop()
        self.temp.cleanup()

    def worker(self, method, path, payload=None):
        self.assertEqual(path, '/inspect/episodes')
        index = payload['offset']
        return {'items': [{'episode_index': index, 'num_frames': 20, 'tasks': ['按按钮'], 'videos': []}]}

    def test_failed_qc_cannot_create_task(self):
        self.dataset['status'] = 'QC_FAIL'
        with self.assertRaises(HTTPException) as error:
            annotation.create_task(annotation.TaskCreate(dataset_id='DS000001'))
        self.assertEqual(error.exception.status_code, 409)
        self.assertEqual(annotation_store.list_tasks(), [])

    def test_create_annotate_submit_and_review(self):
        created = annotation.create_task(annotation.TaskCreate(dataset_id='DS000001'))
        task_id = created['id']
        data = annotation.get_item(task_id, 0)
        self.assertEqual(data['item']['status'], 'DRAFT')
        payload = annotation.AnnotationDraft(
            instruction='按下按钮', outcome='SUCCESS',
            phases=[annotation.Phase(code='CONTACT', start_frame=5, end_frame=8)])
        self.assertEqual(annotation.save_item(task_id, 0, payload)['phases'][0]['end_frame'], 8)
        self.assertEqual(annotation.submit_item(task_id, 0)['status'], 'SUBMITTED')
        self.assertEqual(annotation.review_item(task_id, 0,
                         annotation.ReviewDecision(decision='APPROVE'))['status'], 'APPROVED')
        self.assertEqual(annotation.task_api(task_id)['task']['counts']['approved'], 1)


if __name__ == '__main__':
    unittest.main()
