"""MLX90614 control adapted from the user's temp.py; no power at startup."""
import csv
import math
import logging
import threading
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path
from statistics import median


class PID:
    def __init__(self, kp, ki, kd, target):
        self.kp, self.ki, self.kd, self.target = kp, ki, kd, target
        self.integral = 0.0
        self.last = None

    def update(self, value, now):
        error = self.target - value
        derivative = 0.0
        if self.last:
            dt = max(now - self.last[0], 0.001)
            derivative = (error - self.last[1]) / dt
            candidate = max(-1000, min(1000, self.integral + error * dt))
            predicted = self.kp * error + self.ki * candidate + self.kd * derivative
            # Do not store more integral while it pushes output further into saturation.
            if 0 <= predicted <= 100 or predicted > 100 and error < 0 or predicted < 0 and error > 0:
                self.integral = candidate
        self.last = now, error
        return max(0, min(100, self.kp * error + self.ki * self.integral + self.kd * derivative))


class Heater:
    def __init__(self, config, hardware_factory, clock=time.monotonic):
        self.c, self.factory, self.clock = config, hardware_factory, clock
        self.lock = threading.RLock()
        self.quit = threading.Event()
        self.hw = None
        self.thread = None
        self.history = deque(maxlen=1200)
        self.log = None
        self.writer = None
        self.pid = None
        self.deadline = 0
        self.recovery_timeout = config.get('sensor_recovery_s', 5.0)
        self.last_sample = 0
        self.good_batches = 0
        self.state = dict(ready=False, enabled=config['heater_enabled'], active=False,
                          temperature=None, ambient=None, filtered=None, target=None,
                          duty=0, fault=None, remaining_s=0, log=None, assay_id=None,
                          recovering=False, warning=None,
                          gains={k: config[k] for k in ('kp', 'ki', 'kd')})

    def launch(self):
        if self.thread and self.thread.is_alive():
            raise ValueError('Temperature worker is still running')
        self.quit.clear()
        with self.lock:
            self.last_sample = 0
            self.good_batches = 0
            self.state.update(ready=False, active=False, temperature=None, ambient=None,
                              filtered=None, target=None, duty=0, recovering=False, warning=None)
        self.thread = threading.Thread(target=self.run, daemon=True, name='heater')
        self.thread.start()

    def snapshot(self):
        with self.lock:
            s = dict(self.state)
            s['remaining_s'] = max(0, self.deadline-self.clock()) if s['active'] else 0
            return s

    def _off(self):
        if self.hw:
            self.hw.set_duty(0)
        self.state['duty'] = 0

    def _stop(self, fault=None):
        self.state['active'] = False
        self.state['remaining_s'] = 0
        if fault:
            self.state['fault'] = str(fault)
        try:
            self._off()
        finally:
            if self.log:
                self.log.close()
                self.log = self.writer = None

    def stop(self):
        with self.lock:
            self._stop()

    def validate(self, target, duration, gains=None):
        gains = gains if gains is not None else {k: self.c[k] for k in ('kp','ki','kd')}
        if not isinstance(gains, dict) or set(gains) != {'kp','ki','kd'}:
            raise ValueError('Provide Kp, Ki, and Kd')
        if not all(isinstance(x, (float, int)) and not isinstance(x, bool) and math.isfinite(x)
                   for x in (target, duration, *gains.values())):
            raise ValueError('Parameters must be finite numbers')
        if not (0 < target < self.c['cutoff_c'] and 1 <= duration <= self.c['max_duration_s']):
            raise ValueError('Target temperature or duration is out of range')
        if any(not 0 <= x <= 1000 for x in gains.values()) or not any(gains.values()):
            raise ValueError('PID gains must be between 0 and 1000, with at least one nonzero gain')
        return dict(gains)

    def start(self, target, duration, gains=None, context=None):
        gains = self.validate(target, duration, gains)
        context = dict(context or {})
        with self.lock:
            if not self.state['enabled']:
                raise ValueError('Heating is disabled. Check the wiring and enable it in config.json.')
            if not self.state['ready'] or self.clock()-self.last_sample > 2:
                raise ValueError('Temperature sensor is not ready or its readings are stale')
            if self.state['fault']:
                raise ValueError('Resolve the fault first, then select Clear heating fault')
            if self.state['active']:
                raise ValueError('Heating is already active. Stop it first.')
            if self.state['temperature'] >= self.c['cutoff_c']:
                raise ValueError('Temperature exceeds the safety cutoff')
            folder = Path(self.c['data_dir'])
            if context:
                folder = folder/'assays'/context['id']
            folder = folder/'logs'
            folder.mkdir(parents=True, exist_ok=True)
            name = datetime.now(timezone.utc).strftime('heat_%Y%m%dT%H%M%S_%fZ.csv')
            self.log = (folder/name).open('w', newline='', encoding='utf-8')
            self.writer = csv.writer(self.log)
            self.writer.writerow(['utc','object_c','ambient_c','filtered_c','target_c','duty_pct',
                                  'assay_id','assay_name','operator','kp','ki','kd','sample_status','sensor_error'])
            self.log.flush()
            self.pid = PID(gains['kp'], gains['ki'], gains['kd'], target)
            self.context = context
            self.deadline = self.clock()+duration
            self.state.update(active=True, target=target, log=name, duty=0,
                              assay_id=context.get('id'), gains=gains)

    def clear_fault(self):
        with self.lock:
            if self.state['active'] or not self.state['ready'] or self.clock()-self.last_sample > 2:
                raise ValueError('Heating must be stopped and valid, recent temperature readings are required')
            if self.state['temperature'] >= self.c['cutoff_c']:
                raise ValueError('Temperature still exceeds the safety cutoff')
            self.state['fault'] = None

    def sensor_error(self, message):
        with self.lock:
            self._off()
            self.good_batches = 0
            self.state.update(ready=False, recovering=True, warning=str(message),
                              temperature=None, ambient=None)
            if self.pid:
                self.pid.last = None
            logging.warning('Temperature feedback paused: %s', message)
            if self.writer:
                self.writer.writerow([datetime.now(timezone.utc).isoformat(), '', '', '',
                    self.state['target'], 0, self.context.get('id',''), '', '',
                    self.pid.kp, self.pid.ki, self.pid.kd, 'retrying', str(message)])
                self.log.flush()
            if self.clock()-self.last_sample >= self.recovery_timeout:
                self._stop(f'Temperature feedback did not recover within {self.recovery_timeout:g} seconds. Heating is latched off.')

    def sample(self, readings):
        """Every sample must be valid; cutoff checks raw peaks before smoothing."""
        with self.lock:
            # Overtemperature must never be hidden by another invalid sample.
            if any(math.isfinite(o) and o >= self.c['cutoff_c'] for o,a in readings):
                self._stop('Infrared temperature reached the safety cutoff')
            if not readings or any(not math.isfinite(o) or not math.isfinite(a)
                                   or not -40 < o < 300 or not -40 < a < 125 for o,a in readings):
                self.sensor_error('Invalid infrared temperature reading; output off while retrying')
                return
            recovered = self.state['recovering']
            if recovered:
                if self.clock()-self.last_sample >= self.recovery_timeout and self.state['active']:
                    self._stop(f'Temperature feedback did not recover within {self.recovery_timeout:g} seconds. Heating is latched off.')
                self.good_batches += 1
                if self.good_batches < 2:
                    self._off()
                    return
                self.state.update(recovering=False, warning=None)
                self.state['filtered'] = None
                if self.pid:
                    self.pid.last = None
                logging.info('Temperature feedback recovered after two valid batches')
            obj, amb = median(o for o,a in readings), median(a for o,a in readings)
            old = self.state['filtered']
            ema = obj if old is None else self.c['ema']*obj+(1-self.c['ema'])*old
            self.last_sample = self.clock()
            self.state.update(ready=True, temperature=obj, ambient=amb, filtered=ema)
            if max(o for o,a in readings) >= self.c['cutoff_c']:
                self._stop('Infrared temperature reached the safety cutoff')
            if self.state['active']:
                if self.clock() >= self.deadline:
                    self._stop()
                else:
                    requested = self.pid.update(ema, self.clock())
                    if ema < self.pid.target:
                        # Retain the boost far below target; taper its floor over the final 3 C.
                        floor = self.c['min_duty'] * min(1, (self.pid.target-ema)/3)
                        requested = max(requested, floor)
                    self.state['duty'] = min(requested, 100,
                                             self.state['duty']+self.c['slew'])
            self.history.append(dict(t=time.time(), temperature=obj, target=self.state['target'],
                                     duty=self.state['duty']))
            if self.writer:
                # Prefix formula-like user text for safe viewing in spreadsheet programs.
                def cell(value):
                    return "'"+value if value.lstrip().startswith(('=', '+', '-', '@')) else value
                self.writer.writerow([datetime.now(timezone.utc).isoformat(), obj, amb, ema,
                                      self.state['target'], self.state['duty'],
                                      self.context.get('id',''), cell(self.context.get('name','')),
                                      cell(self.context.get('operator','')),
                                      self.pid.kp,self.pid.ki,self.pid.kd,
                                      'recovered' if recovered else 'valid',''])
                self.log.flush()

    def output(self):
        with self.lock:
            if not self.state['active']:
                self._off()
                return 0
            if self.clock() >= self.deadline:
                self._stop()
                return 0
            if self.clock()-self.last_sample >= self.recovery_timeout:
                self._stop('Temperature reading timed out')
                return 0
            if self.clock()-self.last_sample >= 2 and not self.state['recovering']:
                self.sensor_error('Temperature sample stale; output off while retrying')
            if self.state['recovering'] or not self.state['ready']:
                self._off()
                return 0
            duty = self.state['duty']
            self.hw.set_duty(100 if self.c['burst'] and duty else duty)
            return duty

    def run(self):
        try:
            self.hw = self.factory(self.c)
            index = 0
            while not self.quit.is_set():
                start = self.clock()
                if index % self.c['sample_every'] == 0:
                    # Sensor I/O happens only with the heater physically off.
                    with self.lock:
                        self.hw.set_duty(0)
                    try:
                        readings = []
                        for _ in range(self.c['samples']):
                            if self.quit.wait(self.c['off_window_s']/self.c['samples']):
                                break
                            readings.append(self.hw.read())
                        if not self.quit.is_set():
                            self.sample(readings)
                    except Exception as exc:
                        self.sensor_error(f'MLX90614 read failed: {exc}')
                duty = self.output()
                remaining = max(0, self.c['cycle_s']-(self.clock()-start))
                if self.c['burst']:
                    self.quit.wait(remaining*duty/100)
                    with self.lock:
                        self.hw.set_duty(0)
                    self.quit.wait(remaining*(1-duty/100))
                else:
                    self.quit.wait(remaining)
                index += 1
        except Exception as exc:
            with self.lock:
                self.state.update(ready=False, fault=f'Temperature control initialization or operation failed: {exc}')
        finally:
            with self.lock:
                self._stop()
                self.state['ready'] = False
            if self.hw:
                with self.lock:
                    try:
                        self.hw.close()
                    finally:
                        self.hw = None
                        self.state.update(temperature=None, ambient=None, filtered=None)

    def close(self):
        self.quit.set()
        self.stop()
        if self.thread:
            self.thread.join(3)
