"""Persistent assay records, coordinated runs, and recoverable archive deletion."""
import json
import math
import re
import shutil
import threading
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def duration_seconds(value, maximum):
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        raise ValueError('Duration must be a finite number of minutes')
    seconds = value * 60
    if not 6 <= seconds <= maximum:
        raise ValueError(f'Duration must be between 0.1 and {maximum / 60:g} minutes')
    return seconds


def write_json(path, data):
    pending = path.with_suffix('.tmp')
    pending.write_text(json.dumps(data, indent=2, ensure_ascii=False), encoding='utf-8')
    pending.replace(path)


class Archive:
    def __init__(self, root):
        self.root = Path(root).resolve()
        self.assays = self.root / 'assays'
        self.trash = self.root / '.trash'
        for folder in (self.assays, self.trash, self.root/'photos', self.root/'logs'):
            folder.mkdir(parents=True, exist_ok=True)

    def folder(self, assay_id='unassigned'):
        if assay_id == 'unassigned':
            return self.root
        if not isinstance(assay_id, str) or not re.fullmatch(r'\d{8}T\d{6}Z_[0-9a-f]{12}', assay_id):
            raise ValueError('Invalid assay ID')
        folder = self.assays / assay_id
        if folder.is_symlink() or not folder.is_dir():
            raise ValueError('Assay not found')
        return folder

    def file(self, assay_id, kind, name):
        patterns = {
            'photos': r'\d{8}T\d{6}_\d{6}Z(?:\.thumb)?\.jpg',
            'logs': r'heat_\d{8}T\d{6}_\d{6}Z\.csv',
        }
        if kind not in patterns or not isinstance(name, str) or not re.fullmatch(patterns[kind], name):
            raise ValueError('Invalid file name')
        folder = self.folder(assay_id)/kind
        path = folder/name
        if folder.is_symlink() or path.is_symlink() or not path.is_file():
            raise ValueError('File not found')
        return path

    def create(self, record):
        record = dict(record)
        record['id'] = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ_')+uuid.uuid4().hex[:12]
        folder = self.assays/record['id']
        folder.mkdir()
        (folder/'photos').mkdir()
        (folder/'logs').mkdir()
        self.save(record)
        return record

    def save(self, record):
        write_json(self.folder(record['id'])/'assay.json', record)

    def records(self):
        result = []
        for path in self.assays.glob('*/assay.json'):
            if path.is_symlink() or path.parent.is_symlink():
                continue
            try:
                record = json.loads(path.read_text(encoding='utf-8'))
                if record['id'] != path.parent.name:
                    continue
                result.append(record)
            except (OSError, ValueError, KeyError):
                continue
        return sorted(result, key=lambda r:r['id'], reverse=True)

    def summaries(self):
        records = self.records()
        records.append(dict(id='unassigned', name='Unassigned', operator='', status='standalone',
                            started_at=None, reason='Standalone captures and earlier data'))
        for record in records:
            folder = self.folder(record['id'])
            record['photo_count'] = sum(not p.name.endswith('.thumb.jpg') for p in (folder/'photos').glob('*.jpg'))
            record['log_count'] = sum(1 for _ in (folder/'logs').glob('heat_*.csv'))
        return records

    def delete(self, assay_id, kind, name=None):
        if kind == 'assay':
            if assay_id == 'unassigned':
                raise ValueError('Delete individual unassigned files instead')
            paths = [self.folder(assay_id)]
        else:
            path = self.file(assay_id, kind, name)
            paths = [path]
            if kind == 'photos':
                if name.endswith('.thumb.jpg'):
                    raise ValueError('Delete the original photo, not its thumbnail')
                paths += [p for p in (path.with_suffix('.thumb.jpg'),path.with_suffix('.json')) if p.exists()]
        if any(p.is_symlink() for p in paths):
            raise ValueError('Symbolic links cannot be deleted through the archive')
        trash_id = uuid.uuid4().hex
        destination = self.trash/trash_id
        destination.mkdir()
        entries = [dict(original=str(p.relative_to(self.root)), stored=str(i)) for i,p in enumerate(paths)]
        manifest = dict(id=trash_id, assay_id=assay_id, kind=kind, deleted_at=utc_now(), entries=entries)
        write_json(destination/'manifest.json', manifest)
        moved = []
        try:
            for path, entry in zip(paths, entries):
                shutil.move(str(path), str(destination/entry['stored']))
                moved.append((path, destination/entry['stored']))
        except BaseException:
            for path, stored in reversed(moved):
                shutil.move(str(stored), str(path))
            raise
        return trash_id

    def restore(self, trash_id):
        if not isinstance(trash_id,str) or not re.fullmatch(r'[0-9a-f]{32}',trash_id):
            raise ValueError('Invalid trash ID')
        folder = self.trash/trash_id
        if folder.is_symlink() or not (folder/'manifest.json').is_file():
            raise ValueError('Deleted item not found')
        manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
        if manifest.get('restored_at'):
            raise ValueError('This item has already been restored')
        pairs = []
        for item in manifest['entries']:
            original = self.root/item['original']
            source = folder/item['stored']
            if not original.resolve().is_relative_to(self.root) or not source.resolve().is_relative_to(folder):
                raise ValueError('Invalid trash path')
            if original.exists() or not original.parent.is_dir() or not source.exists():
                raise ValueError('Restore the assay folder first, or resolve a conflicting file')
            pairs.append((source,original))
        moved=[]
        try:
            for source,original in pairs:
                shutil.move(str(source),str(original))
                moved.append((source,original))
            manifest['restored_at']=utc_now()
            write_json(folder/'manifest.json',manifest)
        except BaseException:
            for source,original in reversed(moved):
                shutil.move(str(original),str(source))
            raise


class Assays:
    def __init__(self, config, heater, camera, clock=time.monotonic):
        self.c,self.heater,self.camera,self.clock=config,heater,camera,clock
        self.archive=Archive(config['data_dir'])
        self.lock=threading.RLock()
        self.quit=threading.Event()
        self.thread=None
        self.active=None
        self.last=None
        self.error=None
        # A restart never resumes hardware or claims that a interrupted run completed.
        for record in self.archive.records():
            if record['status'] in ('starting','running'):
                record.update(status='interrupted',ended_at=utc_now(),reason='Service restarted during this assay')
                self.archive.save(record)

    def launch(self):
        self.thread=threading.Thread(target=self.run,daemon=True,name='assay-supervisor')
        self.thread.start()

    def standalone(self):
        if self.active:
            raise ValueError('An assay owns the instruments. Stop the assay before using independent controls.')

    def start(self, data):
        for key,maximum in (('name',120),('operator',80)):
            if not isinstance(data.get(key),str) or not data[key].strip() or len(data[key].strip())>maximum:
                raise ValueError(f'{key.capitalize()} is required (maximum {maximum} characters)')
        seconds=duration_seconds(data.get('duration_minutes'),self.c['max_duration_s'])
        gains=self.heater.validate(data.get('target'),seconds,data.get('pid'))
        interval=data.get('interval')
        self.camera.validate(interval,1)
        count=math.ceil(seconds/interval)
        self.camera.validate(interval,count)
        with self.lock:
            self.standalone()
            h,c=self.heater.snapshot(),self.camera.snapshot()
            if h['active'] or c['running'] or c['busy'] or c.get('pending'):
                raise ValueError('Stop independent heating and acquisition before starting an assay')
            if not c['ready']:
                raise ValueError('Camera not ready')
            record=self.archive.create(dict(name=data['name'].strip(),operator=data['operator'].strip(),
                target=data['target'],duration_minutes=data['duration_minutes'],interval_s=interval,
                expected_photos=count,pid=gains,started_at=utc_now(),ended_at=None,status='starting',reason=None))
            self.active=record
            self.error=None
            try:
                self.camera.light(False)
                self.heater.start(data['target'],seconds,gains,context=record)
                self.deadline=self.clock()+seconds
                self.camera.start(interval,count,context=record,deadline=self.heater.deadline)
                record.update(status='running',log=self.heater.snapshot()['log'])
                self.archive.save(record)
            except BaseException as exc:
                self.finish('failed',str(exc))
                raise
            return dict(record)

    def finish(self, status, reason=None):
        with self.lock:
            if not self.active:
                return
            try:
                self.heater.stop()
            finally:
                self.camera.stop()
            record=dict(self.active)
            record.update(status=status,reason=reason,ended_at=utc_now(),
                          captured=self.camera.snapshot()['captured'])
            self.active=None
            self.last=record
            self.archive.save(record)

    def tick(self):
        with self.lock:
            if not self.active:
                return
            h,c=self.heater.snapshot(),self.camera.snapshot()
            if h['fault'] or c['error'] or not h['ready'] or not c['ready']:
                self.finish('failed',h['fault'] or c['error'] or 'An instrument became unavailable')
            elif self.clock() >= self.deadline:
                self.finish('completed')
            elif not h['active']:
                # The heater timer is authoritative and starts just before the supervisor timer.
                if h['remaining_s']==0 and self.heater.clock()>=self.heater.deadline:
                    self.finish('completed')
                else:
                    self.finish('stopped','Heating stopped before the assay ended')

    def snapshot(self):
        with self.lock:
            return dict(active=dict(self.active) if self.active else None,last=self.last,
                        remaining_s=max(0,self.deadline-self.clock()) if self.active else 0,error=self.error)

    def assert_deletable(self, assay_id):
        if self.active and self.active['id']==assay_id:
            raise ValueError('Cannot delete files from a running assay')
        c,h=self.camera.snapshot(),self.heater.snapshot()
        if (c['busy'] or c['running'] or c.get('pending')) and (c.get('assay_id') or 'unassigned')==assay_id:
            raise ValueError('Wait for acquisition to finish before deleting files')
        if h['active'] and (h.get('assay_id') or 'unassigned')==assay_id:
            raise ValueError('Stop heating before deleting its log')

    def run(self):
        while not self.quit.wait(0.25):
            try:
                self.tick()
            except Exception as exc:
                self.error=f'Assay supervisor failed: {exc}'
                try:
                    self.finish('failed',self.error)
                finally:
                    # Never leave an active output running after supervisor failure.
                    self.heater.stop()
                    self.camera.stop()

    def close(self):
        self.quit.set()
        self.finish('interrupted','Service stopped during this assay')
        if self.thread:
            self.thread.join(2)
