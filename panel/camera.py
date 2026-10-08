import io
import json
import math
import shutil
import threading
import time
from datetime import datetime, timezone
from pathlib import Path


class Camera:
    def __init__(self, config, temperature):
        self.c, self.temperature = config, temperature
        self.lock = threading.RLock()
        self.quit = threading.Event()
        self.cancel = threading.Event()
        self.cam = self.led = None
        self.thread = None
        self.frame = None
        self.frame_at = 0
        self.lease = 0
        self.generation = 0
        self.job = None
        self.single = False
        self.state = dict(ready=False, error=None, preview_light=False, running=False,
                          captured=0, count=0, interval_s=30, latest=None, busy=False,
                          assay_id=None)
        self.photos = Path(config['data_dir'])/'photos'
        self.photos.mkdir(parents=True, exist_ok=True)

    def launch(self):
        if self.thread and self.thread.is_alive():
            raise ValueError('Camera worker is still running')
        self.quit.clear()
        self.cancel.clear()
        with self.lock:
            self.frame = None
            self.frame_at = 0
            self.state.update(ready=False, error=None, busy=False, running=False)
        self.thread = threading.Thread(target=self.run, daemon=True, name='camera')
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.state, pending=self.single)

    def light(self, enabled, renew=False):
        with self.lock:
            if enabled and not self.state['ready']:
                raise ValueError('Camera not ready')
            if renew and (not enabled or time.monotonic() >= self.lease):
                raise ValueError('Excitation light is off. Enable it manually to resume.')
            self.lease = time.monotonic()+15 if enabled else 0
            self.state['preview_light'] = bool(enabled)
            if not enabled and self.led and not self.state['busy']:
                self.led.off()

    def validate(self, interval, count):
        if (isinstance(interval, bool) or not isinstance(interval,(int,float))
            or not math.isfinite(interval) or not 2 <= interval <= 86400):
            raise ValueError('Capture interval must be between 2 and 86400 seconds')
        if isinstance(count, bool) or not isinstance(count,int) or not 1 <= count <= 100000:
            raise ValueError('Photo count must be an integer between 1 and 100000')

    def start(self, interval, count, context=None, deadline=None):
        self.validate(interval, count)
        context = dict(context or {})
        with self.lock:
            if not self.state['ready']:
                raise ValueError('Camera not ready')
            if self.job or self.single or self.state['busy']:
                raise ValueError('An acquisition job is already running')
            self.cancel.clear()
            self.generation += 1
            self.job = dict(interval=interval, count=count, next=time.monotonic(),
                            generation=self.generation, context=context, deadline=deadline)
            self.state.update(running=True, captured=0, count=count, interval_s=interval,
                              error=None, assay_id=context.get('id'))

    def capture(self):
        with self.lock:
            if not self.state['ready']:
                raise ValueError('Camera not ready')
            if self.job or self.single or self.state['busy']:
                raise ValueError('Wait for the current capture to finish')
            self.cancel.clear()
            self.single = True
            self.state.update(assay_id=None, error=None)

    def stop(self):
        # Wake warmup immediately; generation prevents a stopped job being revived.
        self.cancel.set()
        with self.lock:
            self.generation += 1
            self.job = None
            self.single = False
            self.lease = 0
            self.state.update(running=False, preview_light=False)
            if self.led:
                self.led.off()

    def save(self, context=None, deadline=None):
        context = dict(context or {})
        folder = self.photos if not context else Path(self.c['data_dir'])/'assays'/context['id']/'photos'
        folder.mkdir(parents=True, exist_ok=True)
        if shutil.disk_usage(folder).free < 250*1024*1024:
            raise RuntimeError('Less than 250 MB of disk space remains. Acquisition stopped.')
        with self.lock:
            if self.cancel.is_set() or (deadline is not None and time.monotonic() >= deadline):
                return False
            self.state['busy'] = True
            self.led.on()
        try:
            if self.cancel.wait(self.c['led_warmup_s']):
                return False
            name = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S_%fZ')
            # queue=False ensures a fresh exposure after illumination warmup.
            request = self.cam.capture_request()
            try:
                if self.cancel.is_set() or (deadline is not None and time.monotonic() >= deadline):
                    return False
                request.save('main', str(folder/(name+'.pending')), format='JPEG')
                picture = request.make_image('main')
                picture.thumbnail((480,270))
                picture.save(folder/(name+'.thumb.jpg'), 'JPEG', quality=80)
                meta = dict(id=name, utc=datetime.now(timezone.utc).isoformat(),
                            temperature=self.temperature(), camera=request.get_metadata(),
                            assay_id=context.get('id'), assay_name=context.get('name'),
                            operator=context.get('operator'))
                (folder/(name+'.json')).write_text(json.dumps(meta,default=str),encoding='utf-8')
                (folder/(name+'.pending')).replace(folder/(name+'.jpg'))
                with self.lock:
                    self.state['latest'] = name
                return True
            finally:
                request.release()
        finally:
            with self.lock:
                self.state['busy'] = False
                if time.monotonic() >= self.lease or self.cancel.is_set():
                    self.led.off()

    def open_hardware(self):
        from picamera2 import Picamera2
        from libcamera import controls
        from gpiozero import DigitalOutputDevice
        self.led = DigitalOutputDevice(self.c['led_gpio'],
                                      active_high=not self.c['led_active_low'], initial_value=False)
        self.cam = Picamera2()
        self.cam.configure(self.cam.create_still_configuration(
            main={'size':(self.c['photo_width'],self.c['photo_height'])},
            buffer_count=3, queue=False))
        camera_controls = {'AeEnable':True,'AwbEnable':True}
        if 'AfMode' in self.cam.camera_controls:
            camera_controls.update(AfMode=controls.AfModeEnum.Manual,
                                   LensPosition=self.c['lens_position'])
        self.cam.set_controls(camera_controls)
        self.cam.start()

    def run(self):
        try:
            self.open_hardware()
            with self.lock:
                self.state['ready'] = True
            while not self.quit.is_set():
                start = time.monotonic()
                with self.lock:
                    light = start < self.lease
                    self.state['preview_light'] = light
                    self.led.value = light
                    if self.job and self.job.get('deadline') is not None and start >= self.job['deadline']:
                        self.job = None
                        self.state['running'] = False
                    job = dict(self.job) if self.job and start >= self.job['next'] else None
                    single = self.single
                    self.single = False
                    if job or single:
                        self.state['busy'] = True
                if job or single:
                    try:
                        saved = self.save(job.get('context') if job else None,
                                          job.get('deadline') if job else None)
                        with self.lock:
                            if saved and job and self.job and job['generation'] == self.generation:
                                self.state['captured'] += 1
                                if self.state['captured'] >= job['count']:
                                    self.job = None
                                    self.state['running'] = False
                                else:
                                    # Skip missed deadlines; never queue a catch-up burst.
                                    self.job['next'] = max(start+job['interval'],time.monotonic())
                    except Exception as exc:
                        self.stop()
                        with self.lock:
                            self.state['error'] = str(exc)
                    finally:
                        with self.lock:
                            self.state['busy'] = False
                request = self.cam.capture_request()
                try:
                    picture = request.make_image('main')
                    picture.thumbnail((960,540))
                    output = io.BytesIO()
                    picture.save(output, format='JPEG', quality=78)
                    with self.lock:
                        self.frame, self.frame_at = output.getvalue(), time.monotonic()
                finally:
                    request.release()
                self.quit.wait(max(0,0.25-(time.monotonic()-start)))
        except Exception as exc:
            with self.lock:
                self.state['error'] = f'Camera initialization or operation failed: {exc}'
        finally:
            self.stop()
            with self.lock:
                self.state['ready'] = False
            if self.led:
                with self.lock:
                    self.led.close()
                    self.led = None
            if self.cam:
                try:
                    self.cam.close()
                finally:
                    self.cam = None
            with self.lock:
                self.frame = None
                self.frame_at = 0

    def close(self):
        self.quit.set()
        self.stop()
        if self.thread:
            self.thread.join(5)
