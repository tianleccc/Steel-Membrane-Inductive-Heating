import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from panel.app import create_app
from panel.assays import Assays, duration_seconds
from panel.camera import Camera
from panel.heating import Heater


class Hardware:
    def __init__(self): self.duty=0
    def set_duty(self, duty): self.duty=duty


class AssayTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.config=json.loads(Path('config.example.json').read_text())
        self.config.update(data_dir=self.tmp.name,heater_enabled=True,led_warmup_s=0)
        self.now=100.0
        self.heater=Heater(self.config,None,clock=lambda:self.now)
        self.heater.hw=Hardware()
        self.heater.sample([(25,23)]*3)
        self.camera=Camera(self.config,self.heater.snapshot)
        self.camera.state['ready']=True
        self.app=create_app(self.config,self.heater,self.camera)
        self.manager=self.app.extensions['assays']
        self.manager.clock=lambda:self.now
        self.client=self.app.test_client()
        self.token=re.search(r'name="panel-token" content="([^"]+)"',self.client.get('/').text).group(1)
        self.payload=dict(name='Same name / trial',operator='Alice',duration_minutes=0.1,
                          target=40,interval=2,pid={'kp':8,'ki':0.05,'kd':0.1})

    def tearDown(self):
        self.manager.close()
        self.heater.close()
        self.camera.close()
        self.tmp.cleanup()

    def post(self, path, data=None):
        return self.client.post('/api/'+path,json=data or {},headers={'X-Panel-Token':self.token})

    def start(self, name=None):
        payload=dict(self.payload)
        if name is not None:
            payload['name']=name
        response=self.post('assays/start',payload)
        self.assertEqual(response.status_code,200,response.text)
        return response.json

    def photo(self, assay_id):
        path=self.manager.archive.folder(assay_id)/'photos'/'20261007T120000_000001Z.jpg'
        path.write_bytes(b'original')
        path.with_suffix('.thumb.jpg').write_bytes(b'thumbnail')
        path.with_suffix('.json').write_text(json.dumps(dict(utc='2026-10-07T12:00:00Z',temperature={'temperature':42})))
        return path

    def test_minutes_and_pid_api(self):
        response=self.post('heater/start',dict(target=40,duration_minutes=2,pid=self.payload['pid']))
        self.assertEqual(response.status_code,200,response.text)
        self.assertEqual(self.heater.deadline,220)
        self.assertEqual(self.heater.snapshot()['gains'],self.payload['pid'])
        self.assertEqual(duration_seconds(0.1,7200),6)
        for value in (True,None,float('nan'),-1,121):
            with self.assertRaises(ValueError): duration_seconds(value,7200)

    def test_old_page_cannot_silently_use_seconds(self):
        self.assertEqual(self.post('heater/start',dict(target=40,duration=300,duty_cap=30)).status_code,400)
        self.assertFalse(self.heater.snapshot()['active'])

    def test_completed_assay_has_bound_log_and_settings(self):
        record=self.start()
        self.assertEqual(record['expected_photos'],3)
        self.assertEqual(self.camera.job['context']['operator'],'Alice')
        self.assertEqual(self.camera.job['deadline'],106)
        self.heater.sample([(26,23)]*3)
        log=self.manager.archive.folder(record['id'])/'logs'/record['log']
        text=log.read_text()
        self.assertIn('assay_name,operator,kp,ki,kd',text)
        self.assertIn('Same name / trial,Alice,8,0.05,0.1',text)
        self.now=106
        self.manager.tick()
        self.assertIsNone(self.manager.active)
        self.assertEqual(self.manager.archive.records()[0]['status'],'completed')
        self.assertFalse(self.heater.snapshot()['active'])
        self.assertFalse(self.camera.snapshot()['running'])
        self.assertEqual(self.heater.hw.duty,0)

    def test_failed_second_instrument_rolls_back_heating(self):
        with patch.object(self.camera,'start',side_effect=ValueError('Camera start failed')):
            self.assertEqual(self.post('assays/start',self.payload).status_code,400)
        self.assertFalse(self.heater.snapshot()['active'])
        self.assertEqual(self.heater.hw.duty,0)
        self.assertEqual(self.manager.archive.records()[0]['status'],'failed')

    def test_sensor_failure_does_not_cancel_assay(self):
        self.start()
        self.heater.sample([])
        self.manager.tick()
        self.assertIsNotNone(self.manager.active)
        self.now+=5.1
        self.heater.output()
        self.manager.tick()
        self.assertTrue(self.camera.snapshot()['running'])
        self.assertIsNotNone(self.manager.active)
        self.assertEqual(self.heater.hw.duty,0)

    def test_camera_failure_stops_heating(self):
        self.start()
        self.camera.state['error']='Disk full'
        self.manager.tick()
        self.assertFalse(self.heater.snapshot()['active'])
        self.assertEqual(self.manager.last['reason'],'Disk full')

    def test_running_assay_blocks_manual_starts_and_deletion(self):
        record=self.start()
        for route,data in [('heater/start',dict(target=40,duration_minutes=1)),
                           ('camera/start',dict(interval=2,count=1)),('camera/capture',{}),
                           ('camera/light',dict(enabled=True)),
                           ('archive/delete',dict(kind='assay',assay_id=record['id']))]:
            self.assertEqual(self.post(route,data).status_code,400,route)
        self.assertEqual(self.post('stop').status_code,200)
        self.assertEqual(self.manager.last['status'],'stopped')

    def test_duplicate_names_rejected_across_operators_and_normalized_spelling(self):
        first=self.start()
        self.post('assays/stop')
        self.payload['operator']='Bob'
        for name in ('Same name / trial', '  SAME   name / trial  ', 'Ｓａｍｅ name / trial'):
            response=self.post('assays/start',dict(self.payload,name=name))
            self.assertEqual(response.status_code,400,response.text)
            self.assertIn('name already exists',response.json['error'])
            self.assertFalse(self.heater.snapshot()['active'])
            self.assertFalse(self.camera.snapshot()['running'])
            self.assertEqual(len(self.manager.archive.records()),1)
        second=self.start('Different assay')
        self.assertNotEqual(first['id'],second['id'])
        rows=self.client.get('/api/assays?operator=Alice').json['items']
        self.assertEqual([r['id'] for r in rows],[first['id']])
        logs=self.client.get('/api/logs?operator=Alice').json['items']
        self.assertEqual([r['assay_id'] for r in logs],[first['id']])

    def test_deleted_assay_name_reusable_and_folder_permanently_removed(self):
        record=self.start(); self.post('assays/stop')
        deleted=self.post('archive/delete',dict(kind='assay',assay_id=record['id']))
        self.assertEqual(deleted.status_code,200)
        self.assertIsNone(deleted.json['trash_id'])
        self.assertFalse((self.manager.archive.assays/record['id']).exists())
        self.assertEqual(self.post('assays/start',self.payload).status_code,200)

    def test_photo_delete_and_restore_preserve_other_assays(self):
        first=self.start(); self.post('assays/stop')
        second=self.start('Second assay'); self.post('assays/stop')
        one,two=self.photo(first['id']),self.photo(second['id'])
        response=self.post('archive/delete',dict(kind='photos',assay_id=first['id'],name=one.name))
        self.assertEqual(response.status_code,200,response.text)
        self.assertFalse(one.exists()); self.assertFalse(one.with_suffix('.json').exists())
        self.assertTrue(two.exists())
        self.assertEqual(self.post('archive/restore',dict(trash_id=response.json['trash_id'])).status_code,200)
        self.assertEqual(one.read_bytes(),b'original')
        self.assertEqual(one.with_suffix('.thumb.jpg').read_bytes(),b'thumbnail')

    def test_log_download_delete_and_restore(self):
        record=self.start(); self.post('assays/stop')
        response=self.client.get(f"/logs/{record['log']}?assay_id={record['id']}")
        self.assertEqual(response.status_code,200)
        self.assertIn('attachment',response.headers['Content-Disposition'])
        response.close()
        result=self.post('archive/delete',dict(kind='logs',assay_id=record['id'],name=record['log']))
        self.assertEqual(result.status_code,200)
        self.assertFalse(self.client.get('/api/logs').json['items'])
        self.assertEqual(self.post('archive/restore',dict(trash_id=result.json['trash_id'])).status_code,200)
        self.assertEqual(len(self.client.get('/api/logs').json['items']),1)

    def test_folder_permanent_delete_and_path_traversal(self):
        record=self.start(); self.post('assays/stop')
        photo=self.photo(record['id'])
        result=self.post('archive/delete',dict(kind='assay',assay_id=record['id']))
        self.assertEqual(result.status_code,200)
        self.assertFalse(photo.exists())
        self.assertIsNone(result.json['trash_id'])
        self.assertEqual(self.post('archive/restore',dict(trash_id=result.json['trash_id'])).status_code,400)
        self.assertFalse(photo.exists())
        for assay_id in ('../../', '/etc', 'unassigned/../assays'):
            self.assertEqual(self.post('archive/delete',dict(kind='assay',assay_id=assay_id)).status_code,400)
        with self.assertRaises(ValueError): self.manager.archive.file('unassigned','logs','../config.json')

    def test_old_deleted_assay_trash_purged_without_touching_live_or_manual_files(self):
        old=self.start(); self.post('assays/stop')
        photo=self.photo(old['id'])
        trash=self.manager.archive.move_to_trash([photo.parent.parent],old['id'],'assay')
        live=self.start('Live folder'); self.post('assays/stop')
        other=self.photo(live['id'])
        manual=self.photo('unassigned')
        manual_trash=self.manager.archive.delete('unassigned','photos',manual.name)
        self.manager.archive.assert_unique_name(old['name'])
        result=self.manager.archive.purge_assay_trash()
        self.assertEqual(result['folders'],1)
        self.assertGreater(result['bytes'],0)
        self.assertFalse((self.manager.archive.trash/trash).exists())
        self.assertTrue(other.exists())
        self.assertTrue((self.manager.archive.trash/manual_trash).exists())

    def test_clear_all_logs_and_undo_preserve_photos(self):
        record=self.start(); self.post('assays/stop')
        photo=self.photo(record['id'])
        manual=self.manager.archive.folder()/'logs'/'heat_20261007T120000_000001Z.csv'
        manual.write_text('temperature\n25\n')
        result=self.post('archive/clear-logs')
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(result.json['count'],2)
        self.assertFalse(self.client.get('/api/logs').json['items'])
        self.assertTrue(photo.exists())
        self.assertTrue((photo.parent.parent/'assay.json').exists())
        self.assertEqual(self.post('archive/restore',dict(trash_id=result.json['trash_id'])).status_code,200)
        self.assertEqual(len(self.client.get('/api/logs').json['items']),2)
        self.assertEqual(manual.read_text(),'temperature\n25\n')

    def test_clear_logs_checks_all_folders_before_deleting(self):
        self.start(); self.post('assays/stop')
        self.start('Second assay')
        self.assertEqual(self.post('archive/clear-logs').status_code,400)
        self.assertEqual(len(self.client.get('/api/logs').json['items']),2)
        self.post('assays/stop')
        with patch.object(self.app.extensions['exports'],'protect',side_effect=ValueError('Export running')):
            self.assertEqual(self.post('archive/clear-logs').status_code,400)
        self.assertEqual(len(self.client.get('/api/logs').json['items']),2)

    def test_clear_logs_empty_and_token_required(self):
        self.assertEqual(self.client.post('/api/archive/clear-logs',json={}).status_code,403)
        result=self.post('archive/clear-logs')
        self.assertEqual(result.json['count'],0)
        self.assertIsNone(result.json['trash_id'])

    def test_restart_marks_unfinished_assay_interrupted(self):
        record=self.start()
        self.heater.stop(); self.camera.stop()
        other=Assays(self.config,self.heater,self.camera)
        self.assertIsNone(other.active)
        self.assertEqual(other.archive.records()[0]['status'],'interrupted')
        self.manager.active=None

    def test_unassigned_legacy_files_remain_accessible(self):
        photo=self.photo('unassigned')
        self.assertEqual(self.client.get('/api/photos').json['items'][0]['id'],photo.stem)
        with self.client.get('/photos/'+photo.name) as response:
            self.assertEqual(response.data,b'original')
        self.assertTrue(any(r['id']=='unassigned' for r in self.client.get('/api/assays').json['items']))

    def test_camera_saves_in_assay_folder_with_metadata(self):
        record=self.start()
        class LED:
            def on(self): pass
            def off(self): pass
            def close(self): pass
        class Image:
            def thumbnail(self,size): pass
            def save(self,path,*args,**kwargs): Path(path).write_bytes(b'thumbnail')
        class Request:
            def save(self,stream,path,**kwargs):
                import cv2
                import numpy as np
                ok, data = cv2.imencode('.jpg', np.zeros((2592,4608,3),np.uint8))
                assert ok
                Path(path).write_bytes(data.tobytes())
            def make_image(self,stream): return Image()
            def get_metadata(self): return {'ExposureTime':10000}
            def release(self): pass
        class Sensor:
            def capture_request(self): return Request()
            def close(self): pass
        self.camera.led=LED(); self.camera.cam=Sensor()
        with patch('panel.camera.time.monotonic',return_value=101):
            self.assertTrue(self.camera.save(record,106))
        folder=self.manager.archive.folder(record['id'])/'photos'/'amplification'
        metadata=json.loads(next(folder.glob('*.json')).read_text())
        self.assertEqual(metadata['assay_id'],record['id'])
        self.assertEqual(metadata['operator'],'Alice')
        self.assertEqual(len(list(self.camera.photos.glob('*.jpg'))),0)
        with patch('panel.camera.time.monotonic',return_value=106):
            self.assertFalse(self.camera.save(record,106))



    def test_assay_continues_after_transient_sensor_error(self):
        record=self.start()
        self.now+=.5
        self.heater.sensor_error('temporary I2C error')
        self.manager.tick()
        self.assertEqual(self.manager.active['id'],record['id'])
        self.assertEqual(self.heater.output(),0)
        for _ in range(2):
            self.now+=.5
            self.heater.sample([(25,23)]*3)
            self.manager.tick()
        self.assertEqual(self.manager.active['id'],record['id'])
        self.assertTrue(self.camera.snapshot()['running'])
        self.assertTrue(self.heater.snapshot()['active'])
        self.assertFalse(self.heater.snapshot()['recovering'])

