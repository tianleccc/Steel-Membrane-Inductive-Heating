"""Instrument sleep; the web server and archive remain available."""
import json
import threading
import time
from .assays import write_json


class Power:
    def __init__(self, assays):
        self.assays = assays
        self.heater, self.camera = assays.heater, assays.camera
        self.path = assays.archive.root/'instrument-power.json'
        self.mode = 'awake'
        self.error = None
        self.worker = None
        if self.path.exists():
            try:
                if json.loads(self.path.read_text()).get('sleeping'):
                    self.mode = 'asleep'
            except (OSError, ValueError):
                self.mode = 'asleep'
                self.error = 'Could not read saved power state. Wake instruments manually.'

    def snapshot(self):
        with self.assays.lock:
            return dict(mode=self.mode, error=self.error)

    def require_awake(self):
        if self.mode != 'awake':
            raise ValueError('Wake instruments and wait until they are ready first')

    def boot(self):
        if self.mode == 'awake':
            self.heater.launch()
            self.camera.launch()

    def request(self, action):
        with self.assays.lock:
            if action not in ('sleep', 'wake'):
                raise ValueError('Choose sleep or wake')
            if self.worker and self.worker.is_alive():
                raise ValueError('Instrument power transition is still in progress')
            if action == 'sleep' and self.mode == 'asleep' or action == 'wake' and self.mode == 'awake':
                return self.snapshot()
            h, c = self.heater.snapshot(), self.camera.snapshot()
            if self.assays.active or h['active'] or c['running'] or c['busy'] or c.get('pending'):
                raise ValueError('Stop the assay, heating and acquisition before changing instrument power')
            if action == 'wake' and any(d.thread and d.thread.is_alive() for d in (self.heater,self.camera)):
                raise ValueError('Previous hardware workers have not stopped. Wait or restart the service.')
            write_json(self.path, dict(sleeping=action == 'sleep'))
            self.mode = 'sleeping' if action == 'sleep' else 'waking'
            self.error = None
            self.worker = threading.Thread(target=self.transition,args=(action,),daemon=True,name='instrument-power')
            self.worker.start()
            return self.snapshot()

    def transition(self, action):
        try:
            if action == 'sleep':
                try:
                    self.heater.close()
                finally:
                    self.camera.close()
                if any(d.thread and d.thread.is_alive() for d in (self.heater,self.camera)):
                    raise RuntimeError('Hardware worker did not stop. Wake is blocked until it exits.')
                mode = 'asleep'
            else:
                self.heater.launch()
                self.camera.launch()
                deadline = time.monotonic()+20
                while time.monotonic()<deadline:
                    if self.heater.snapshot()['ready'] and self.camera.snapshot()['ready']:
                        break
                    time.sleep(.2)
                mode = 'awake'
                if not self.heater.snapshot()['ready'] or not self.camera.snapshot()['ready']:
                    self.error = 'Wake finished with an unavailable instrument. Check the instrument error below.'
            with self.assays.lock:
                self.mode = mode
        except Exception as error:
            # Never start a second owner after an incomplete shutdown/initialization.
            try:
                self.heater.close()
            finally:
                self.camera.close()
            with self.assays.lock:
                self.mode = 'error'
                self.error = str(error)

    def close(self):
        if self.worker:
            self.worker.join(25)
        try:
            self.heater.close()
        finally:
            self.camera.close()
