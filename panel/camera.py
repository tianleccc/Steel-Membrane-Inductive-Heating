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
                          captured=0, count=0, interval_s=30, latest=None, busy=False)
        self.photos = Path(config['data_dir'])/'photos'
        self.photos.mkdir(parents=True, exist_ok=True)

    def launch(self):
        self.thread = threading.Thread(target=self.run, daemon=True, name='camera')
        self.thread.start()

    def snapshot(self):
        with self.lock:
            return dict(self.state)

    def light(self, enabled):
        with self.lock:
            if enabled and not self.state['ready']:
                raise ValueError('相机未就绪')
            self.lease = time.monotonic()+15 if enabled else 0
            self.state['preview_light'] = bool(enabled)
            if not enabled and self.led and not self.state['busy']:
                self.led.off()

    def start(self, interval, count):
        if (isinstance(interval, bool) or not isinstance(interval,(int,float))
            or not math.isfinite(interval) or not 2 <= interval <= 86400):
            raise ValueError('拍照间隔必须为 2–86400 秒')
        if isinstance(count, bool) or not isinstance(count,int) or not 1 <= count <= 100000:
            raise ValueError('照片数量必须为 1–100000 的整数')
        with self.lock:
            if not self.state['ready']:
                raise ValueError('相机未就绪')
            if self.job or self.single or self.state['busy']:
                raise ValueError('拍照任务正在运行')
            self.cancel.clear()
            self.generation += 1
            self.job = dict(interval=interval, count=count, next=time.monotonic(),
                            generation=self.generation)
            self.state.update(running=True, captured=0, count=count, interval_s=interval, error=None)

    def capture(self):
        with self.lock:
            if not self.state['ready']:
                raise ValueError('相机未就绪')
            if self.job or self.single or self.state['busy']:
                raise ValueError('请等待当前拍照任务完成')
            self.cancel.clear()
            self.single = True

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

    def save(self):
        if shutil.disk_usage(self.photos).free < 250*1024*1024:
            raise RuntimeError('磁盘剩余空间不足 250 MB，已停止拍照')
        with self.lock:
            if self.cancel.is_set():
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
                if self.cancel.is_set():
                    return False
                request.save('main', str(self.photos/(name+'.pending')), format='JPEG')
                picture = request.make_image('main')
                picture.thumbnail((480,270))
                picture.save(self.photos/(name+'.thumb.jpg'), 'JPEG', quality=80)
                meta = dict(id=name, utc=datetime.now(timezone.utc).isoformat(),
                            temperature=self.temperature(), camera=request.get_metadata())
                (self.photos/(name+'.json')).write_text(json.dumps(meta,default=str),encoding='utf-8')
                (self.photos/(name+'.pending')).replace(self.photos/(name+'.jpg'))
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

    def run(self):
        try:
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
            with self.lock:
                self.state['ready'] = True
            while not self.quit.is_set():
                start = time.monotonic()
                with self.lock:
                    light = start < self.lease
                    self.state['preview_light'] = light
                    self.led.value = light
                    job = dict(self.job) if self.job and start >= self.job['next'] else None
                    single = self.single
                    self.single = False
                if job or single:
                    try:
                        saved = self.save()
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
                self.state['error'] = f'相机初始化/运行失败: {exc}'
        finally:
            self.stop()
            with self.lock:
                self.state['ready'] = False
            if self.led:
                self.led.close()
            if self.cam:
                self.cam.close()

    def close(self):
        self.quit.set()
        self.stop()
        if self.thread:
            self.thread.join(5)
