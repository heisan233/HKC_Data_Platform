"""Focused safeguards for dataset replacement and record cleanup."""
import json
import os
import shutil
import sqlite3
import tempfile
import unittest
from pathlib import Path

import data_management as management
import dataset_store as store


class DataManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.original = {module: {name: getattr(module, name) for name in names}
                         for module, names in ((store, ('ROOT', 'RAW', 'DB')),
                                               (management, ('ROOT', 'RAW', 'QC_DB', 'QC_REPORTS')))}
        store.ROOT = management.ROOT = self.root
        store.RAW = management.RAW = self.root / 'raw'
        store.DB = self.root / 'system/platform.sqlite'
        management.QC_DB = self.root / 'system/qc_jobs.sqlite'
        management.QC_REPORTS = self.root / 'outputs/qc'
        store.init_store()

    def tearDown(self):
        for module, values in self.original.items():
            for name, value in values.items():
                setattr(module, name, value)
        self.temp.cleanup()

    def dataset_dir(self, path, label):
        (path / 'meta').mkdir(parents=True)
        (path / 'meta/info.json').write_text(json.dumps({
            'codebase_version': 'v3.0', 'robot_type': 'zhiyan',
            'total_episodes': 1, 'total_frames': 20, 'fps': 20, 'features': {},
        }))
        (path / 'version.txt').write_text(label)

    def registered_dataset(self):
        dataset_id = store.create_dataset('原名称', 'zhiyan')
        path = store.RAW / dataset_id
        self.dataset_dir(path, 'old')
        store.set_status(dataset_id, 'QC_PASS')
        return dataset_id, path

    def test_display_update_scan_and_version_swap(self):
        dataset_id, current = self.registered_dataset()
        item = management.update_display(dataset_id, '新名称', '备注', ['抓取', '抓取', '训练'])
        self.assertEqual(item['tags'], ['抓取', '训练'])
        self.assertFalse(management.scan_dataset(dataset_id)['scan_error'])
        update_id = 'a' * 32
        stage = store.RAW / '.updates' / dataset_id / update_id
        self.dataset_dir(stage, 'new')
        management.stage_update(dataset_id, update_id)
        management.set_update_run(update_id, 'run-1')
        management.apply_update(update_id)
        self.assertEqual((current / 'version.txt').read_text(), 'new')
        self.assertEqual(management.get_update(update_id)['status'], 'APPLIED')
        self.assertFalse((self.root / 'upload_tmp/update_backups' / update_id).exists())

    def test_recover_interrupted_swap(self):
        dataset_id, current = self.registered_dataset()
        update_id = 'b' * 32
        stage = store.RAW / '.updates' / dataset_id / update_id
        self.dataset_dir(stage, 'new')
        management.stage_update(dataset_id, update_id)
        management.set_update_run(update_id, 'run-2')
        backup = self.root / 'upload_tmp/update_backups' / update_id
        backup.parent.mkdir(parents=True)
        with store.db() as con:
            con.execute("UPDATE dataset_updates SET status='SWAPPING' WHERE id=?", (update_id,))
        os.replace(current, backup)
        os.replace(stage, current)
        management.recover_interrupted_updates()
        self.assertEqual((current / 'version.txt').read_text(), 'old')
        self.assertEqual((stage / 'version.txt').read_text(), 'new')
        self.assertEqual(management.get_update(update_id)['status'], 'QC_QUEUED')

    def test_record_purge_requires_absent_directory_and_keeps_number(self):
        dataset_id, current = self.registered_dataset()
        management.QC_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(management.QC_DB) as con:
            con.execute('CREATE TABLE jobs (run_id TEXT PRIMARY KEY,dataset_id TEXT,status TEXT)')
            con.execute('INSERT INTO jobs VALUES (?,?,?)', ('run-3', dataset_id, 'COMPLETED'))
        report = management.QC_REPORTS / dataset_id / 'run-3.json'
        report.parent.mkdir(parents=True)
        report.write_text('{}')
        store.set_status(dataset_id, 'DELETED')
        with self.assertRaises(ValueError):
            management.purge_record(dataset_id)
        shutil.rmtree(current)
        management.purge_record(dataset_id)
        self.assertIsNone(store.get_dataset(dataset_id))
        self.assertTrue(report.exists())
        with sqlite3.connect(management.QC_DB) as con:
            self.assertEqual(con.execute('SELECT COUNT(*) FROM jobs').fetchone()[0], 0)
        self.assertNotEqual(store.create_dataset('下一个', 'zhiyan'), dataset_id)

    def test_optional_report_cleanup(self):
        dataset_id, current = self.registered_dataset()
        shutil.rmtree(current)
        with sqlite3.connect(management.QC_DB) as con:
            con.execute('CREATE TABLE jobs (run_id TEXT PRIMARY KEY,dataset_id TEXT,status TEXT)')
        report = management.QC_REPORTS / dataset_id / 'report.json'
        report.parent.mkdir(parents=True)
        report.write_text('{}')
        management.purge_record(dataset_id, purge_reports=True)
        self.assertFalse(report.exists())

    def test_upload_completion_time_is_recorded_once(self):
        dataset_id = store.create_dataset('待上传', 'zhiyan')
        self.assertIsNone(store.get_dataset(dataset_id)['uploaded_at'])
        store.set_status(dataset_id, 'READY')
        uploaded_at = store.get_dataset(dataset_id)['uploaded_at']
        self.assertIsNotNone(uploaded_at)
        store.set_status(dataset_id, 'QC_PASS')
        self.assertEqual(store.get_dataset(dataset_id)['uploaded_at'], uploaded_at)

    def test_existing_dataset_keeps_unknown_upload_time(self):
        path = store.RAW / 'legacy'
        self.dataset_dir(path, 'old')
        store.init_store()
        with store.db() as con:
            row = con.execute("SELECT id, created_at, uploaded_at FROM datasets WHERE raw_relpath='legacy'").fetchone()
        self.assertIsNotNone(row['created_at'])
        self.assertIsNone(row['uploaded_at'])
        store.set_status(row['id'], 'READY')
        self.assertIsNone(store.get_dataset(row['id'])['uploaded_at'])

    def test_record_cleanup_requires_confirmation_without_code(self):
        from fastapi import HTTPException
        from routers import data as data_router

        dataset_id, directory = self.registered_dataset()
        store.set_status(dataset_id, 'DELETED')
        shutil.rmtree(directory)
        management.QC_DB.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(management.QC_DB) as con:
            con.execute('CREATE TABLE jobs (run_id TEXT PRIMARY KEY,dataset_id TEXT,status TEXT)')
        with self.assertRaises(HTTPException) as error:
            data_router.delete_record(dataset_id, data_router.PurgeRequest(confirmed=False))
        self.assertEqual(error.exception.status_code, 400)
        result = data_router.delete_record(dataset_id, data_router.PurgeRequest(confirmed=True))
        self.assertTrue(result['deleted'])
        self.assertIsNone(store.get_dataset(dataset_id))


if __name__ == '__main__':
    unittest.main()
