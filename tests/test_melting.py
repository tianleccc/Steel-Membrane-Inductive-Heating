import csv
import io
import json
import unittest
import zipfile
from unittest.mock import patch

import test_assays
import test_exports
from panel.assays import Assays
from panel.reaction import (ReactionSession, read_rows, write_measurement, melting_series,
                            FIELDS, LEGACY_FIELDS)


class MeltingTests(unittest.TestCase):
    setUp = test_assays.AssayTests.setUp
    tearDown = test_assays.AssayTests.tearDown
    post = test_assays.AssayTests.post
    start = test_assays.AssayTests.start
    wait = test_exports.ExportTests.wait

    def begin(self):
        self.payload['melting'] = dict(start=40,end=42,increment=1)
        return self.start()

    def advance(self,seconds):
        self.now += seconds
        self.heater.sample([(25,23)]*3)
        self.manager.tick()

    def done_photo(self):
        self.camera.job = None
        self.camera.state.update(running=False,busy=False,captured=1)
        self.manager.tick()

    def test_skip_waits_for_camera_and_preserves_assay(self):
        record = self.begin()
        self.camera.state['busy'] = True
        self.assertEqual(self.post('assays/skip').status_code,200)
        self.manager.tick()
        self.assertEqual(self.manager.active['stage'],'transition')
        self.assertFalse(self.heater.snapshot()['active'])
        self.camera.state['busy'] = False
        with patch('panel.camera.time.monotonic',return_value=self.now):
            self.manager.tick()
        self.assertEqual(self.manager.active['id'],record['id'])
        self.assertEqual(self.camera.job['next'],self.now+60)
        self.assertEqual(self.camera.job['context']['stage'],'melting')
        self.assertEqual(self.post('assays/skip').status_code,400)
        self.assertEqual(len(self.manager.archive.records()),1)

    def test_natural_transition_and_every_point_has_60_second_delay(self):
        self.begin()
        self.advance(6)
        self.assertEqual(self.manager.active['stage'],'transition')
        with patch('panel.camera.time.monotonic',return_value=self.now):
            self.manager.tick()
        reaction_id = self.camera.reaction.id
        self.assertEqual(self.heater.snapshot()['target'],40)
        for index in range(3):
            self.advance(61)
            with patch('panel.camera.time.monotonic',return_value=self.now):
                self.done_photo()
            if index < 2:
                self.assertEqual(self.camera.job['next'],self.now+60)
                self.assertEqual(self.heater.snapshot()['target'],41+index)
                self.assertEqual(self.camera.reaction.id,reaction_id)
                self.assertIsNone(self.heater.startup_phase)
        self.assertIsNone(self.manager.active)
        self.assertTrue(self.manager.last['melting_completed'])
        self.assertEqual(self.manager.last['melting_captured'],3)
        self.assertFalse(self.heater.snapshot()['active'])
        self.assertEqual(self.heater.hw.duty,0)

    def test_invalid_melting_rejected_before_record_or_heat(self):
        for settings in [True,{},dict(start=40,end=40,increment=1),dict(start=40,end=110,increment=1),
                         dict(start=40,end=42,increment=0),dict(start=40,end=42,increment=True),
                         dict(start=40,end=42,increment=float('nan')),dict(start=10,end=100,increment=.1)]:
            self.assertEqual(self.post('assays/start',dict(self.payload,melting=settings)).status_code,400)
            self.assertFalse(self.heater.snapshot()['active'])
            self.assertEqual(self.manager.archive.records(),[])
        settings=self.manager.validate_melting(dict(start=40,end=42.5,increment=1),self.payload['pid'])
        self.assertEqual(settings['targets'],[40,41,42,42.5])

    def test_skip_without_melting_and_stop_in_transition(self):
        self.assertEqual(self.post('assays/skip').status_code,400)
        self.start()
        self.assertEqual(self.post('assays/skip').status_code,400)
        self.post('assays/stop')
        self.payload['name']='Another assay'
        self.begin(); self.post('assays/skip'); self.post('stop')
        self.manager.tick()
        self.assertFalse(self.heater.snapshot()['active'])
        self.assertFalse(self.camera.snapshot()['running'])
        self.assertFalse(self.manager.last['melting_completed'])

    def test_capture_timeout_and_sensor_fault_stop_melting(self):
        self.begin(); self.post('assays/skip'); self.manager.tick()
        self.advance(90)
        self.assertEqual(self.manager.last['status'],'failed')
        self.assertFalse(self.heater.snapshot()['active'])
        self.payload['name']='Sensor fault assay'
        self.begin(); self.post('assays/skip'); self.manager.tick()
        self.heater.state['fault']='Sensor failed'
        self.manager.tick()
        self.assertEqual(self.manager.last['status'],'failed')
        self.assertFalse(self.camera.snapshot()['running'])

    def test_restart_does_not_resume_melting_or_publish_derivative(self):
        record=self.begin(); self.post('assays/skip'); self.manager.tick()
        self.heater.stop(); self.camera.stop()
        other=Assays(self.config,self.heater,self.camera)
        self.assertEqual(other.archive.records()[0]['status'],'interrupted')
        self.assertFalse(other.archive.records()[0]['melting_completed'])
        self.assertIsNone(other.active)

    def test_stage_files_download_export_delete_restore_and_legacy_schema(self):
        record=self.begin(); self.post('assays/stop')
        folder=self.manager.archive.folder(record['id'])
        rows=[]
        for index,stage in enumerate(['amplification','melting']):
            directory=folder/'photos'/stage; directory.mkdir(exist_ok=True)
            photo=directory/f'20261029T120000_00000{index}Z.jpg'; photo.write_bytes(b'photo')
            row={k:0 for k in FIELDS}
            row.update(Image=photo.name,UTC='2026-10-29T12:00:00Z',Assay_ID=record['id'],
                       Reaction_ID=stage,Stage=stage,Setpoint_C=40,Measured_C=39.8)
            write_measurement(photo.with_suffix('.reaction.csv'),row); rows.append(row)
        self.assertEqual(len(read_rows(folder)),2)
        for stage in ['amplification','melting']:
            result=self.client.get(f"/api/photos?assay_id={record['id']}&stage={stage}")
            self.assertEqual(result.json['total'],1)
            with self.client.get(f"/photos/{rows[stage=='melting']['Image']}?assay_id={record['id']}") as response:
                self.assertEqual(response.data,b'photo')
        result=self.client.get(f"/api/reaction?assay_id={record['id']}").json
        self.assertEqual(len(result['points']),1)
        self.assertEqual(len(result['melting']['points']),1)
        self.assertEqual(result['melting']['derivative'],[])
        self.camera.state.update(assay_id=record['id'],reaction_id='melting')
        current=self.client.get('/api/reaction/current').json
        self.assertEqual(len(current['points']),1)
        self.assertEqual(len(current['melting']['points']),1)
        self.camera.state.update(assay_id=None,reaction_id=None)
        current=self.client.get('/api/reaction/current').json
        self.assertEqual(current['points'],[])
        self.assertEqual(current['melting']['points'],[])
        with self.client.get(f"/reaction.csv?assay_id={record['id']}") as response:
            self.assertEqual(len(list(csv.DictReader(io.StringIO(response.text)))),2)
        job=self.wait(self.post('exports',dict(assay_id=record['id'],mode='photos')).json)
        self.assertEqual(job['status'],'complete',job)
        with self.client.get('/exports/'+job['id']+'/download') as response:
            with zipfile.ZipFile(io.BytesIO(response.data)) as bundle:
                names=bundle.namelist()
                self.assertTrue(any('/amplification/' in n for n in names))
                self.assertTrue(any('/melting/' in n for n in names))
                exported=list(csv.DictReader(io.StringIO(bundle.read(names[-1]).decode())))
                for row in exported:
                    self.assertIn(job['name']+'/'+row['Export_image'],names)
        deleted=self.post('archive/delete',dict(assay_id=record['id'],kind='photos',name=rows[1]['Image']))
        self.assertEqual(len(read_rows(folder)),1)
        self.assertEqual(self.post('archive/restore',dict(trash_id=deleted.json['trash_id'])).status_code,200)
        self.assertEqual(len(read_rows(folder)),2)
        # Legacy CSVs still load with their original immutable values.
        photo=folder/'photos'/'20261029T130000_000000Z.jpg'; photo.write_bytes(b'legacy')
        row={k:rows[0][k] for k in LEGACY_FIELDS}; row['Image']=photo.name
        with photo.with_suffix('.reaction.csv').open('w',newline='') as stream:
            writer=csv.DictWriter(stream,fieldnames=LEGACY_FIELDS); writer.writeheader(); writer.writerow(row)
        self.assertEqual(len(read_rows(folder)),3)

    def test_transition_timeout_camera_error_and_retarget_fault(self):
        self.begin(); self.camera.state['busy']=True; self.post('assays/skip')
        self.advance(30)
        self.assertEqual(self.manager.last['status'],'failed')
        self.camera.state['busy']=False
        self.payload['name']='Camera error assay'
        self.begin(); self.post('assays/skip'); self.manager.tick()
        self.camera.state['error']='Capture write error'
        self.manager.tick()
        self.assertFalse(self.heater.snapshot()['active'])
        self.assertFalse(self.manager.last['melting_completed'])
        with self.assertRaises(ValueError): self.heater.retarget(41)


class DerivativeTests(unittest.TestCase):
    def test_nonuniform_gradient_sign_no_amplification_baseline_and_completion_gate(self):
        rows=[]
        for x in [40,41,42,42.5]:
            row=dict(Image=str(x),UTC='',Time_min=x-40,Stage='melting',Setpoint_C=x,Measured_C=x-.2)
            row.update({f'Raw_Region_{i}':10000-x*x for i in range(1,5)})
            rows.append(row)
        self.assertEqual(melting_series(rows)['derivative'],[])
        values=melting_series(rows,True)['derivative']
        self.assertEqual(values[1]['derivative'],[82]*4)
        self.assertEqual(values[2]['derivative'],[84]*4)
        self.assertEqual(values[0]['derivative'],[81]*4)
        self.assertEqual(values[-1]['derivative'],[84.5]*4)
        self.assertEqual(melting_series(rows[:1],True)['derivative'],[])
