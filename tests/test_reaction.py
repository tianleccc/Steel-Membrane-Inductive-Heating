import csv
import io
import json
from pathlib import Path
import tempfile
import shutil
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import zipfile

import cv2
import numpy as np

from panel.reaction import ReactionSession, settings, read_rows, write_measurement, series
import test_assays
import test_exports


class ReactionMathTests(unittest.TestCase):
    def test_adjusted_centers_form_square_and_preserve_order(self):
        regions=settings()['coordinates']
        points=np.array([r['center'] for r in regions])
        edges=np.roll(points,-1,axis=0)-points
        lengths=np.linalg.norm(edges,axis=1)
        np.testing.assert_allclose(lengths,lengths[0])
        self.assertAlmostEqual(float(np.dot(edges[0],edges[1])),0)
        self.assertEqual([r['radius'] for r in regions],[108]*4)
        self.assertLess(points[0,1],points[2,1])
        self.assertLess(points[3,0],points[1,0])

    def test_roi_crop_matches_offline_full_frame_algorithm_and_causal_ema(self):
        config=dict(photo_width=4608,photo_height=2592)
        session=ReactionSession(config)
        raw_history=[]; results=[]
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'image.jpg'
            for step,level in enumerate([100,80,180,220]):
                image=np.zeros((2592,4608,3),np.uint8)
                for region in settings()['coordinates']:
                    center=tuple(round(v) for v in region['center'])
                    cv2.circle(image,center,90,(0,level,0),-1)
                    cv2.circle(image,(center[0]+80,center[1]),20,(0,0,255),-1)
                cv2.imwrite(str(path),image)
                hsv=cv2.cvtColor(cv2.imread(str(path)),cv2.COLOR_BGR2HSV)
                green=cv2.inRange(hsv,np.array([35,50,50]),np.array([85,255,255]))
                expected=[]
                for region in settings()['coordinates']:
                    mask=np.zeros(hsv.shape[:2],np.uint8)
                    cv2.circle(mask,tuple(round(v) for v in region['center']),round(region['radius']),255,-1)
                    expected.append(int(np.sum(hsv[:,:,2][(green==255)&(mask==255)],dtype=np.int64)*2))
                measurement=session.analyze(path,path.name,'2026-10-28T15:00:00Z','example',100+step*37)
                session.commit(measurement)
                row=measurement[0]; results.append(row); raw_history.append(expected)
                self.assertEqual([row[f'Raw_Region_{i}'] for i in range(1,5)],expected)
                self.assertAlmostEqual(row['Time_min'],step*37/60)
        for region in range(4):
            smooth=background=None
            for raw,row in zip(raw_history,results):
                smooth=raw[region] if smooth is None else .35*raw[region]+.65*smooth
                background=smooth if background is None else min(background,smooth)
                self.assertAlmostEqual(row[f'Corrected_Region_{region+1}'],max(0,smooth-background))
        self.assertGreater(results[-1]['Corrected_Region_1'],0)
        with self.assertRaises(ValueError): ReactionSession(dict(photo_width=960,photo_height=540))


class ReactionArchiveTests(unittest.TestCase):
    setUp=test_assays.AssayTests.setUp
    tearDown=test_assays.AssayTests.tearDown
    post=test_assays.AssayTests.post
    photo=test_assays.AssayTests.photo
    wait=test_exports.ExportTests.wait

    def measurement(self, assay_id='unassigned', session=None):
        photo=self.photo(assay_id)
        cv2.imwrite(str(photo),np.full((2592,4608,3),(0,120,0),np.uint8))
        session=session or ReactionSession(self.config)
        measured=session.analyze(photo,photo.name,'2026-10-28T15:00:00Z',assay_id,100)
        session.commit(measured)
        path=photo.with_suffix('.reaction.csv')
        write_measurement(path,measured[0])
        return photo,path,measured[0]

    def test_persisted_csv_survives_reload_and_temperature_cleanup(self):
        photo,path,row=self.measurement()
        original=path.read_bytes()
        (photo.parent.parent/'logs'/'heat_20261028T150000_000001Z.csv').write_text('temperature\n25')
        self.assertEqual(self.post('archive/clear-logs').json['count'],1)
        self.assertEqual(path.read_bytes(),original)
        data=self.client.get('/api/reaction').json
        self.assertEqual(len(data['points']),1)
        self.assertEqual(data['points'][0]['corrected'],[0]*4)
        self.assertEqual(data['session_id'],row['Reaction_ID'])
        with self.client.get('/reaction.csv') as response:
            output=list(csv.DictReader(io.StringIO(response.text)))
            self.assertEqual(output[0]['Image'],photo.name)
            self.assertGreater(float(output[0]['Raw_Region_1']),0)
        self.assertEqual(series(read_rows(photo.parent.parent))['session_id'],row['Reaction_ID'])

    def test_photo_delete_restore_moves_csv_together_and_no_independent_delete(self):
        photo,path,_=self.measurement()
        original=path.read_bytes()
        for kind in ('logs','photos','reaction'):
            self.assertEqual(self.post('archive/delete',dict(kind=kind,name=path.name)).status_code,400)
        deleted=self.post('archive/delete',dict(kind='photos',name=photo.name))
        self.assertEqual(deleted.status_code,200)
        self.assertFalse(photo.exists()); self.assertFalse(path.exists())
        self.assertEqual(self.client.get('/api/reaction').json['points'],[])
        self.assertEqual(self.post('archive/restore',dict(trash_id=deleted.json['trash_id'])).status_code,200)
        self.assertEqual(path.read_bytes(),original)

    def test_export_contains_reaction_csv_with_numbered_photo_mapping(self):
        photo,path,row=self.measurement()
        job=self.wait(self.post('exports',dict(assay_id='unassigned',mode='photos')).json)
        self.assertEqual(job['status'],'complete',job)
        with self.client.get('/exports/'+job['id']+'/download') as response:
            with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
                self.assertEqual(bundle.namelist(),['Unassigned/image001.jpg','Unassigned/reaction_log.csv'])
                output=list(csv.DictReader(io.StringIO(bundle.read('Unassigned/reaction_log.csv').decode())))
                self.assertEqual(output[0]['Export_image'],'image001.jpg')
                self.assertEqual(output[0]['Image'],photo.name)
                self.assertEqual(output[0]['Reaction_ID'],row['Reaction_ID'])

    def test_runs_have_independent_baselines_and_invalid_inputs_fail(self):
        one=ReactionSession(self.config); two=ReactionSession(self.config)
        self.assertNotEqual(one.id,two.id)
        self.assertIsNone(two.smooth)
        self.measurement(session=one)
        self.assertEqual(self.client.get('/api/reaction?session_id=unknown').status_code,400)
        self.assertEqual(self.client.get('/api/reaction?assay_id=../bad').status_code,400)
        self.assertEqual(len(self.client.get('/api/reaction/regions').json['coordinates']),4)

    def test_workspace_only_reads_current_session_never_latest_history(self):
        _,_,row=self.measurement()
        self.assertEqual(self.client.get('/api/reaction/current').json['points'],[])
        history=self.client.get('/api/reaction').json['points']
        self.assertEqual(len(history),1)
        self.assertEqual(history[0]['raw'],[row[f'Raw_Region_{i}'] for i in range(1,5)])
        self.camera.state['reaction_id']=row['Reaction_ID']
        self.assertEqual(len(self.client.get('/api/reaction/current').json['points']),1)
        self.camera.state['reaction_id']='new-run-with-no-photos'
        self.assertEqual(self.client.get('/api/reaction/current').json['points'],[])
        self.camera.state.update(reaction_id=None,assay_id=None)
        self.assertEqual(self.client.get('/api/reaction/current').json['points'],[])

    def test_missing_analyzer_blocks_assay_before_heating(self):
        payload=dict(name='preflight',operator='test',duration_minutes=1,target=40,interval=30,pid={'kp':8,'ki':.05,'kd':.1})
        with patch.object(self.camera,'validate_reaction',side_effect=ValueError('Missing analyzer')):
            self.assertEqual(self.post('assays/start',payload).status_code,400)
        self.assertFalse(self.heater.snapshot()['active'])
        self.assertEqual(self.manager.archive.records(),[])

    def test_csv_write_failure_preserves_original_and_surfaces_error(self):
        source=Path(self.tmp.name)/'source.jpg'
        cv2.imwrite(str(source),np.zeros((2592,4608,3),np.uint8))
        picture=SimpleNamespace(thumbnail=lambda size:None,
                                save=lambda path,*args,**kwargs:Path(path).write_bytes(b'thumbnail'))
        request=SimpleNamespace(save=lambda stream,path,**kwargs:shutil.copyfile(source,path),
                                make_image=lambda stream:picture,get_metadata=lambda:{},release=lambda:None)
        self.camera.cam=SimpleNamespace(capture_request=lambda:request,close=lambda:None)
        self.camera.led=SimpleNamespace(on=lambda:None,off=lambda:None,close=lambda:None)
        with patch('panel.camera.write_measurement',side_effect=OSError('CSV write failed')):
            self.assertTrue(self.camera.save())
        photo=self.camera.photos/(self.camera.snapshot()['latest']+'.jpg')
        self.assertEqual(photo.read_bytes(),source.read_bytes())
        self.assertIn('CSV write failed',json.loads(photo.with_suffix('.json').read_text())['reaction_error'])
        self.assertIn('Acquisition continues',self.camera.snapshot()['warning'])
        self.assertIsNone(self.camera.snapshot()['error'])
        self.assertEqual(read_rows(self.manager.archive.root),[])

    def test_melting_photo_with_missing_temperature_keeps_fluorescence(self):
        record=self.manager.archive.create(dict(name='Missing temperature',operator='Test',status='running'))
        record.update(photo_layout='stages',stage='melting',melting_target=65)
        source=Path(self.tmp.name)/'source.jpg'
        cv2.imwrite(str(source),np.full((2592,4608,3),(0,120,0),np.uint8))
        picture=SimpleNamespace(thumbnail=lambda size:None,
                                save=lambda path,*args,**kwargs:Path(path).write_bytes(b'thumbnail'))
        request=SimpleNamespace(save=lambda stream,path,**kwargs:shutil.copyfile(source,path),
                                make_image=lambda stream:picture,get_metadata=lambda:{},release=lambda:None)
        self.camera.cam=SimpleNamespace(capture_request=lambda:request,close=lambda:None)
        self.camera.led=SimpleNamespace(on=lambda:None,off=lambda:None,close=lambda:None)
        self.heater.sensor_error('PEC mismatch')
        self.assertTrue(self.camera.save(record))
        rows=read_rows(self.manager.archive.folder(record['id']))
        self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['Measured_C'],'')
        self.assertEqual(rows[0]['Temperature_status'],'unavailable')
        self.assertEqual(rows[0]['Setpoint_C'],65)
        self.assertGreater(rows[0]['Raw_Region_1'],0)
        self.assertIsNone(self.camera.snapshot()['error'])
