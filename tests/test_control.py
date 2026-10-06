import json
import tempfile
import unittest
from pathlib import Path
from panel.heating import Heater
from panel.camera import Camera
from panel.app import create_app


class Hardware:
    def __init__(self): self.duty=0
    def set_duty(self,value): self.duty=value


class ControlTests(unittest.TestCase):
    def setUp(self):
        self.folder=tempfile.TemporaryDirectory()
        self.c=json.loads(Path('config.example.json').read_text())
        self.c.update(data_dir=self.folder.name,heater_enabled=True)
        self.time=10.0
        self.h=Heater(self.c,None,clock=lambda:self.time)
        self.h.hw=Hardware()
        self.h.sample([(25,23)]*3)

    def tearDown(self):
        self.h.close()
        self.folder.cleanup()

    def test_initial_power_is_zero(self):
        self.assertFalse(self.h.snapshot()['active'])
        self.assertEqual(self.h.output(),0)

    def test_minimum_duty_never_overrides_cap(self):
        self.h.start(70,300,10)
        self.h.sample([(25,23)]*3)
        self.assertEqual(self.h.output(),10)

    def test_raw_peak_cutoff_before_ema_or_median(self):
        self.h.start(70,300,30)
        self.h.sample([(25,23),(111,23),(25,23)])
        self.assertFalse(self.h.snapshot()['active'])
        self.assertTrue(self.h.snapshot()['fault'])
        self.assertEqual(self.h.hw.duty,0)

    def test_sensor_failure_latches_and_never_restarts_automatically(self):
        self.h.start(70,300,30)
        self.h.sample([])
        self.h.sample([(25,23)]*3)
        self.assertFalse(self.h.snapshot()['active'])
        with self.assertRaises(ValueError): self.h.start(70,300,30)
        self.h.clear_fault()
        self.h.start(70,300,30)
        self.assertTrue(self.h.snapshot()['active'])

    def test_nan_fails_closed(self):
        self.h.start(70,300,30)
        self.h.sample([(float('nan'),23)])
        self.assertFalse(self.h.snapshot()['active'])
        self.assertFalse(self.h.snapshot()['ready'])

    def test_duration_expiry_turns_off(self):
        self.h.start(70,1,30)
        self.h.sample([(25,23)]*3)
        self.h.output()
        self.time+=1.1
        self.assertEqual(self.h.output(),0)
        self.assertFalse(self.h.snapshot()['active'])

    def test_stale_sample_turns_off(self):
        self.h.start(70,300,30)
        self.time+=2.1
        self.assertEqual(self.h.output(),0)
        self.assertTrue(self.h.snapshot()['fault'])

    def test_stop_is_immediate(self):
        self.h.start(70,300,30)
        self.h.sample([(25,23)]*3)
        self.h.output()
        self.h.stop()
        self.assertEqual(self.h.hw.duty,0)
        self.assertEqual(self.h.output(),0)

    def test_invalid_parameters_rejected_without_power(self):
        for values in [(float('nan'),300,30),(110,300,30),(50,-1,30),(50,300,101),(None,300,30)]:
            with self.assertRaises(ValueError): self.h.start(*values)
        self.assertEqual(self.h.hw.duty,0)

    def test_disabled_and_unready_refuse_start(self):
        self.h.state['enabled']=False
        with self.assertRaises(ValueError): self.h.start(50,300,30)
        self.h.state.update(enabled=True,ready=False)
        with self.assertRaises(ValueError): self.h.start(50,300,30)

    def test_log_persists_samples_before_stop(self):
        self.h.start(50,300,30)
        self.h.sample([(26,23)]*3)
        path=next((Path(self.folder.name)/'logs').glob('*.csv'))
        self.assertEqual(len(path.read_text().splitlines()),2)

    def test_camera_cancel_does_not_leave_pending_job(self):
        camera=Camera(self.c,self.h.snapshot)
        camera.state['ready']=True
        camera.start(2,2)
        generation=camera.generation
        camera.stop()
        self.assertIsNone(camera.job)
        self.assertTrue(camera.cancel.is_set())
        self.assertGreater(camera.generation,generation)

    def test_web_requires_page_token_and_same_origin(self):
        camera=Camera(self.c,self.h.snapshot)
        app=create_app(self.c,self.h,camera)
        client=app.test_client()
        import re
        token=re.search(r'name="panel-token" content="([^"]+)"',client.get('/').text).group(1)
        self.assertEqual(client.post('/api/stop',json={}).status_code,403)
        self.assertEqual(client.post('/api/stop',json={},headers={'X-Panel-Token':token,'Origin':'http://other.example'}).status_code,403)
        self.assertEqual(client.post('/api/stop',json={},headers={'X-Panel-Token':token}).status_code,200)
        self.assertEqual(client.get('/photos/../../config.json').status_code,404)

    def test_stopped_light_cannot_be_reenabled_by_old_heartbeat(self):
        camera=Camera(self.c,self.h.snapshot)
        camera.state['ready']=True
        camera.light(True)
        camera.stop()
        with self.assertRaises(ValueError): camera.light(True,renew=True)
        camera.light(True)
        self.assertTrue(camera.snapshot()['preview_light'])


if __name__=='__main__': unittest.main()
