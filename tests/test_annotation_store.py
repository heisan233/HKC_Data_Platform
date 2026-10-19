"""Annotation persistence and state-transition checks."""

import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import annotation_store as annotations
import dataset_store


class Phase:
    def __init__(self, code, start_frame, end_frame):
        self.code = code
        self.start_frame = start_frame
        self.end_frame = end_frame

    def model_dump(self):
        return {'code': self.code, 'start_frame': self.start_frame, 'end_frame': self.end_frame}


def draft(phases=None):
    return SimpleNamespace(instruction='按下电梯按钮', outcome='SUCCESS',
                           phases=phases or [], notes='画面清楚')


class AnnotationStoreTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_patch = patch.object(dataset_store, 'DB', Path(self.temp.name) / 'platform.sqlite')
        self.db_patch.start()
        annotations.init_store()
        self.task = annotations.create_task('DS000001', 'run-1', 'DS000001', 2, 'run-1')

    def tearDown(self):
        self.db_patch.stop()
        self.temp.cleanup()

    def test_task_is_idempotent_per_qc_version(self):
        same = annotations.create_task('DS000001', 'run-1', 'DS000001', 2, 'run-1')
        next_version = annotations.create_task('DS000001', 'run-2', 'DS000001', 2, 'run-2')
        self.assertEqual(same['id'], self.task['id'])
        self.assertNotEqual(next_version['id'], self.task['id'])

    def test_submit_reject_rework_approve(self):
        task_id = self.task['id']
        annotations.save_item(task_id, 0, draft([Phase('APPROACH', 0, 10)]), frame_count=20)
        submitted = annotations.submit_item(task_id, 0)
        self.assertEqual((submitted['status'], submitted['revision']), ('SUBMITTED', 1))
        with self.assertRaisesRegex(ValueError, '不能修改'):
            annotations.save_item(task_id, 0, draft(), frame_count=20)
        with self.assertRaisesRegex(ValueError, '原因'):
            annotations.review_item(task_id, 0, 'REJECT')
        self.assertEqual(annotations.review_item(task_id, 0, 'REJECT', '起止帧错误')['status'], 'REWORK')
        annotations.save_item(task_id, 0, draft([Phase('APPROACH', 1, 11)]), frame_count=20)
        self.assertEqual(annotations.submit_item(task_id, 0)['revision'], 2)
        self.assertEqual(annotations.review_item(task_id, 0, 'APPROVE')['status'], 'APPROVED')
        self.assertEqual(annotations.task_counts(task_id, 2)['approved'], 1)
        with self.assertRaisesRegex(ValueError, '只有待审核'):
            annotations.review_item(task_id, 0, 'APPROVE')

    def test_rejects_overlapping_or_out_of_range_phases(self):
        with self.assertRaisesRegex(ValueError, '不能重叠'):
            annotations.save_item(self.task['id'], 0,
                                  draft([Phase('APPROACH', 0, 10), Phase('CONTACT', 9, 12)]), 20)
        with self.assertRaisesRegex(ValueError, '超出'):
            annotations.save_item(self.task['id'], 0, draft([Phase('APPROACH', 0, 21)]), 20)


if __name__ == '__main__':
    unittest.main()
