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

    def test_pid_can_use_full_output_without_user_cap(self):
        self.h.start(70,300)
        self.h.sample([(25,23)]*3)
        self.assertEqual(self.h.output(),100)

    def test_custom_pid_is_used(self):
        self.h.start(30,300,{'kp':8,'ki':0,'kd':0})
        self.h.sample([(25,23)]*3)
        self.assertEqual(self.h.output(),40)
        self.assertEqual(self.h.snapshot()['gains'],{'kp':8,'ki':0,'kd':0})

    def test_raw_peak_cutoff_before_ema_or_median(self):
        self.h.start(70,300)
        self.h.sample([(25,23),(111,23),(25,23)])
        self.assertFalse(self.h.snapshot()['active'])
        self.assertTrue(self.h.snapshot()['fault'])
        self.assertEqual(self.h.hw.duty,0)

    def test_sensor_failure_latches_and_never_restarts_automatically(self):
        self.h.start(70,300)
        self.h.sample([])
        self.time+=self.h.recovery_timeout+.1
        self.h.output()
        self.h.sample([(25,23)]*3)
        self.h.sample([(25,23)]*3)
        self.assertFalse(self.h.snapshot()['active'])
        with self.assertRaises(ValueError): self.h.start(70,300)
        self.h.clear_fault()
        self.h.start(70,300)
        self.assertTrue(self.h.snapshot()['active'])

    def test_nan_fails_closed(self):
        self.h.start(70,300)
        self.h.sample([(float('nan'),23)])
        self.assertTrue(self.h.snapshot()['active'])
        self.assertEqual(self.h.output(),0)
        self.assertFalse(self.h.snapshot()['ready'])

    def test_duration_expiry_turns_off(self):
        self.h.start(70,1)
        self.h.sample([(25,23)]*3)
        self.h.output()
        self.time+=1.1
        self.assertEqual(self.h.output(),0)
        self.assertFalse(self.h.snapshot()['active'])

    def test_stale_sample_turns_off(self):
        self.h.start(70,300)
        self.time+=self.h.recovery_timeout+.1
        self.assertEqual(self.h.output(),0)
        self.assertTrue(self.h.snapshot()['fault'])

    def test_stop_is_immediate(self):
        self.h.start(70,300)
        self.h.sample([(25,23)]*3)
        self.h.output()
        self.h.stop()
        self.assertEqual(self.h.hw.duty,0)
        self.assertEqual(self.h.output(),0)

    def test_invalid_parameters_rejected_without_power(self):
        for values in [(float('nan'),300),(110,300),(50,-1),(50,300,{'kp':-1,'ki':0,'kd':0}),(None,300)]:
            with self.assertRaises(ValueError): self.h.start(*values)
        self.assertEqual(self.h.hw.duty,0)

    def test_disabled_and_unready_refuse_start(self):
        self.h.state['enabled']=False
        with self.assertRaises(ValueError): self.h.start(50,300)
        self.h.state.update(enabled=True,ready=False)
        with self.assertRaises(ValueError): self.h.start(50,300)

    def test_log_persists_samples_before_stop(self):
        self.h.start(50,300)
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
        self.assertIn(client.get('/photos/../../config.json').status_code,(400,404))

    def test_stopped_light_cannot_be_reenabled_by_old_heartbeat(self):
        camera=Camera(self.c,self.h.snapshot)
        camera.state['ready']=True
        camera.light(True)
        camera.stop()
        with self.assertRaises(ValueError): camera.light(True,renew=True)
        camera.light(True)
        self.assertTrue(camera.snapshot()['preview_light'])




    def test_transient_failure_recovers_only_after_two_valid_batches(self):
        self.h.start(70,300)
        deadline=self.h.deadline
        self.time+=.5
        self.h.sensor_error('I2C temporary error')
        self.assertEqual(self.h.output(),0)
        self.assertTrue(self.h.snapshot()['active'])
        self.time+=.5
        self.h.sample([(26,23)]*3)
        self.assertEqual(self.h.output(),0)
        self.assertTrue(self.h.snapshot()['recovering'])
        self.time+=.5
        self.h.sample([(26,23)]*3)
        self.assertGreater(self.h.output(),0)
        self.assertIsNone(self.h.snapshot()['fault'])
        self.assertFalse(self.h.snapshot()['recovering'])
        self.assertEqual(deadline,self.h.deadline)

    def test_late_valid_read_cannot_resume_faulted_heating(self):
        self.h.start(70,300)
        self.h.sensor_error('error')
        self.time+=self.h.recovery_timeout+.1
        self.h.sample([(25,23)]*3)
        self.h.sample([(25,23)]*3)
        self.assertFalse(self.h.snapshot()['active'])
        self.assertTrue(self.h.snapshot()['fault'])
        self.assertEqual(self.h.output(),0)

    def test_mixed_invalid_and_overtemperature_stops_immediately(self):
        self.h.start(70,300)
        self.h.sample([(float('nan'),23),(111,23)])
        self.assertFalse(self.h.snapshot()['active'])
        self.assertIn('cutoff',self.h.snapshot()['fault'])

    def test_repeated_bad_batches_do_not_extend_recovery_window(self):
        self.h.start(70,300)
        for i in range(1,11):
            self.time=10+i*.5
            self.h.sensor_error('error')
            self.h.output()
        self.assertFalse(self.h.snapshot()['active'])
        self.assertTrue(self.h.snapshot()['fault'])

    def test_recovery_after_three_seconds_is_allowed_with_output_off(self):
        self.h.start(70,300)
        self.h.sensor_error('temporary')
        self.time+=3
        self.assertEqual(self.h.output(),0)
        self.assertTrue(self.h.snapshot()['active'])
        self.h.sample([(25,23)]*3)
        self.time+=.5
        self.h.sample([(25,23)]*3)
        self.assertGreater(self.h.output(),0)

    def test_stale_output_is_zero_before_extended_timeout(self):
        self.h.start(70,300)
        self.h.sample([(25,23)]*3)
        self.time+=2.1
        self.assertEqual(self.h.output(),0)
        self.assertTrue(self.h.snapshot()['active'])
        self.assertTrue(self.h.snapshot()['recovering'])

    def test_pid_does_not_accumulate_integral_at_full_power(self):
        from panel.heating import PID
        pid=PID(20,.1,0,65)
        for t in range(100):self.assertEqual(pid.update(25,t),100)
        self.assertEqual(pid.integral,0)
        self.assertEqual(pid.update(66,100),0)

    def test_minimum_duty_tapers_near_target(self):
        self.h.start(65,300,dict(kp=1,ki=0,kd=0))
        self.h.state['filtered']=64.5
        self.h.sample([(64.5,23)]*3)
        self.assertAlmostEqual(self.h.state['duty'],5)
