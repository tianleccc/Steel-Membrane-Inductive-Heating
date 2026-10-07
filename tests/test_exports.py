import io
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch
import zipfile
import test_assays
from panel.exports import usb_drives


class ExportTests(unittest.TestCase):
    setUp = test_assays.AssayTests.setUp
    tearDown = test_assays.AssayTests.tearDown
    post = test_assays.AssayTests.post
    photo = test_assays.AssayTests.photo

    def record(self):
        record = self.manager.archive.create(dict(name='USB / trial', operator='Alice', status='completed'))
        self.photo(record['id'])
        (self.manager.archive.folder(record['id'])/'logs'/'heat_20261007T120000_000001Z.csv').write_text('temperature\n42\n')
        return record['id']

    def wait(self, job):
        end = time.monotonic() + 5
        while time.monotonic() < end:
            state = self.client.get('/api/exports/'+job['id']).json
            if state['status'] != 'running':
                return state
            time.sleep(.01)
        self.fail('Export did not finish')

    def drive(self, path):
        stat = path.stat()
        return dict(id='/dev/test-usb', label='Test USB', mount=str(path),
                    free_bytes=10**10, identity=(stat.st_dev,stat.st_ino))

    def test_zip_contains_all_originals_metadata_no_thumbnails_or_logs(self):
        aid = self.record()
        reply = self.post('exports',dict(assay_id=aid,mode='photos'))
        self.assertEqual(reply.status_code,200,reply.text)
        state = self.wait(reply.json)
        self.assertEqual(state['status'],'complete',state)
        response = self.client.get('/exports/'+state['id']+'/download')
        with zipfile.ZipFile(io.BytesIO(response.data)) as z:
            names=z.namelist()
            self.assertEqual(len(names),3)
            self.assertTrue(any(n.endswith('assay.json') for n in names))
            self.assertFalse(any('thumb' in n or n.endswith('.csv') for n in names))
            self.assertEqual(z.read(next(n for n in names if n.endswith('.jpg'))),b'original')
        response.close()

    def test_usb_exports_complete_folder_without_overwriting(self):
        aid=self.record()
        with tempfile.TemporaryDirectory() as tmp:
            drive=self.drive(Path(tmp))
            self.app.extensions['exports'].drives=lambda:[drive]
            (Path(tmp)/'existing.txt').write_text('keep')
            destinations=[]
            for _ in range(2):
                response=self.post('exports',dict(assay_id=aid,mode='usb',drive_id=drive['id']))
                result=self.wait(response.json)
                self.assertEqual(result['status'],'complete',result)
                destinations.append(result['destination'])
                folder=Path(result['destination'])
                self.assertTrue((folder/'assay.json').is_file())
                self.assertEqual(len(list((folder/'photos').iterdir())),3)
                self.assertEqual(len(list((folder/'logs').iterdir())),1)
            self.assertNotEqual(*destinations)
            self.assertEqual((Path(tmp)/'existing.txt').read_text(),'keep')

    def test_usb_missing_low_space_changed_mount_and_write_failure(self):
        aid=self.record()
        exports=self.app.extensions['exports']
        exports.drives=lambda:[]
        payload=dict(assay_id=aid,mode='usb',drive_id='/dev/test-usb')
        self.assertEqual(self.post('exports',payload).status_code,400)
        with tempfile.TemporaryDirectory() as tmp:
            drive=self.drive(Path(tmp)); exports.drives=lambda:[drive]
            drive['free_bytes']=1
            self.assertEqual(self.post('exports',payload).status_code,400)
            drive['free_bytes']=10**10; drive['identity']=(0,0)
            self.assertEqual(self.wait(self.post('exports',payload).json)['status'],'failed')
            drive.update(self.drive(Path(tmp)))
            with patch('panel.exports.shutil.copyfile',side_effect=OSError('drive disconnected')):
                result=self.wait(self.post('exports',payload).json)
                self.assertEqual(result['status'],'failed')
                self.assertIn('drive disconnected',result['error'])
            self.assertTrue(self.manager.archive.folder(aid).is_dir())

    def test_export_locks_and_active_run_and_traversal(self):
        aid=self.record(); exports=self.app.extensions['exports']
        exports.job=dict(id='test',assay_id=aid,status='running')
        self.assertEqual(self.post('archive/delete',dict(assay_id=aid,kind='assay')).status_code,400)
        self.assertEqual(self.post('archive/restore',dict(trash_id='x')).status_code,400)
        self.assertEqual(self.post('exports',dict(assay_id=aid,mode='photos')).status_code,400)
        exports.job=None
        self.assertEqual(self.post('exports',dict(assay_id='../',mode='photos')).status_code,400)
        active=self.post('assays/start',self.payload).json
        self.assertEqual(self.post('exports',dict(assay_id=active['id'],mode='photos')).status_code,400)
        self.post('assays/stop')
        exports.job=dict(id='test',assay_id='unassigned',status='running')
        self.assertEqual(self.post('camera/capture').status_code,400)
        self.assertEqual(self.post('heater/start',dict(target=40,duration_minutes=1)).status_code,400)

    def test_usb_detection_excludes_internal_disks_and_readonly(self):
        data={'blockdevices':[
          dict(path='/dev/sda',tran='usb',children=[dict(path='/dev/sda1',mountpoints=['/media/pi/USB'],label='USB',ro=False)]),
          dict(path='/dev/mmcblk0p2',tran='mmc',mountpoints=['/'],ro=False),
          dict(path='/dev/sdb1',tran='usb',mountpoints=['/media/pi/RO'],ro=True)]}
        with patch('panel.exports.subprocess.run') as run, patch('panel.exports.Path.is_symlink',return_value=False), patch('panel.exports.Path.is_mount',return_value=True), patch('panel.exports.os.access',return_value=True), patch('panel.exports.Path.stat') as stat, patch('panel.exports.shutil.disk_usage') as usage:
            def lsblk_result(args, **kwargs):
                # Without --tree (and without NAME), real lsblk returns a flat list.
                tree = data if '--tree' in args else {'blockdevices': [dict(path='/dev/sda1', tran=None, mountpoints=['/media/pi/USB'], ro=False)]}
                from types import SimpleNamespace
                return SimpleNamespace(stdout=json.dumps(tree))
            run.side_effect=lsblk_result
            stat.return_value.st_dev=1;stat.return_value.st_ino=2;usage.return_value.free=10**9
            found=usb_drives()
            self.assertEqual([d['id'] for d in found],['/dev/sda1'])

    def test_unsafe_link_rejected(self):
        aid=self.record()
        (self.manager.archive.folder(aid)/'photos'/'leak.json').symlink_to('/etc/passwd')
        self.assertEqual(self.post('exports',dict(assay_id=aid,mode='photos')).status_code,400)
