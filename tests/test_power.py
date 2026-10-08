import json
import tempfile
import threading
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from panel.assays import Archive
from panel.power import Power
from panel.heating import Heater


class Device:
    def __init__(self):
        self.thread=None
        self.stop_event=threading.Event()
        self.reads=0
        self.state=dict(active=False,running=False,busy=False,pending=False,ready=False)
    def snapshot(self): return dict(self.state)
    def launch(self):
        self.stop_event.clear()
        self.state['ready']=True
        def worker():
            while not self.stop_event.wait(.01): self.reads+=1
        self.thread=threading.Thread(target=worker);self.thread.start()
    def close(self):
        self.stop_event.set()
        if self.thread:self.thread.join(1)
        self.state['ready']=False


class PowerTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.assays=SimpleNamespace(heater=Device(),camera=Device(),lock=threading.RLock(),active=None,archive=Archive(self.tmp.name))
        self.power=Power(self.assays)
    def tearDown(self):
        self.power.close();self.tmp.cleanup()
    def transition(self,action):
        self.power.request(action)
        self.power.worker.join(3)
        self.assertFalse(self.power.worker.is_alive())
    def test_sleep_stops_workers_and_repeated_wake_never_resumes_jobs(self):
        self.power.boot()
        for _ in range(3):
            self.transition('sleep')
            self.assertEqual(self.power.mode,'asleep')
            count=self.assays.camera.reads
            time.sleep(.03)
            self.assertEqual(count,self.assays.camera.reads)
            with self.assertRaises(ValueError):self.power.require_awake()
            self.transition('wake')
            self.assertEqual(self.power.mode,'awake')
            self.assertTrue(self.assays.camera.state['ready'])
            self.assertFalse(self.assays.heater.state['active'])
    def test_sleep_is_persisted_and_boot_does_not_open_devices(self):
        self.power.boot();self.transition('sleep')
        reloaded=Power(self.assays);reloaded.boot()
        self.assertEqual(reloaded.mode,'asleep')
        self.assertFalse(self.assays.camera.thread.is_alive())
    def test_active_experiments_and_pending_captures_refuse_sleep(self):
        for device,key in [(self.assays.heater,'active'),(self.assays.camera,'pending'),(self.assays.camera,'busy')]:
            device.state[key]=True
            with self.assertRaises(ValueError):self.power.request('sleep')
            device.state[key]=False
        self.assays.active={'id':'test'}
        with self.assertRaises(ValueError):self.power.request('sleep')
    def test_heater_can_close_and_relaunch_with_fault_retained(self):
        config=json.loads(Path('config.example.json').read_text());config['data_dir']=self.tmp.name
        class Hardware:
            def set_duty(self,value): pass
            def read(self):return 25,23
            def close(self):pass
        heater=Heater(config,lambda _:Hardware())
        try:
            for _ in range(2):
                heater.launch()
                end=time.monotonic()+2
                while not heater.snapshot()['ready'] and time.monotonic()<end:time.sleep(.02)
                self.assertTrue(heater.snapshot()['ready'])
                self.assertFalse(heater.snapshot()['active'])
                heater.state['fault']='Existing fault'
                heater.close()
                self.assertIsNone(heater.hw)
                self.assertFalse(heater.thread.is_alive())
                self.assertEqual(heater.snapshot()['fault'],'Existing fault')
        finally:heater.close()
