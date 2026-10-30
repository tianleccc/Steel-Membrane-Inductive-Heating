"""Persistent assay records, coordinated runs, and recoverable archive deletion."""
import json
import math
import re
import shutil
import threading
import time
import uuid
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from .reaction import photo_folders, photo_files


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
        root = self.folder(assay_id)
        if kind == 'photos':
            candidates = [folder/name for folder in photo_folders(root) if (folder/name).exists()]
            if len(candidates) != 1:
                raise ValueError('File not found or ambiguous')
            folder = candidates[0].parent
        else:
            folder = root/kind
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

    def assert_unique_name(self, name):
        def normalized(value):
            return ' '.join(unicodedata.normalize('NFKC', value).split()).casefold()
        names = [record['name'] for record in self.records()]
        if normalized(name) in {normalized(value) for value in names}:
            raise ValueError('This assay name already exists. Choose a unique name.')

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
            record['photo_count'] = len(photo_files(folder))
            record['log_count'] = sum(1 for _ in (folder/'logs').glob('heat_*.csv'))
        return records

    def delete(self, assay_id, kind, name=None):
        if kind == 'assay':
            if assay_id == 'unassigned':
                raise ValueError('Delete individual unassigned files instead')
            folder = self.folder(assay_id)
            if self.assays.is_symlink() or folder.resolve().parent != self.assays:
                raise ValueError('Invalid assay folder')
            shutil.rmtree(folder)
            self.purge_assay_trash({assay_id})
            return None
        else:
            path = self.file(assay_id, kind, name)
            paths = [path]
            if kind == 'photos':
                if name.endswith('.thumb.jpg'):
                    raise ValueError('Delete the original photo, not its thumbnail')
                paths += [p for p in (path.with_suffix('.thumb.jpg'),path.with_suffix('.json'),
                                      path.with_suffix('.reaction.csv')) if p.exists()]
        return self.move_to_trash(paths, assay_id, kind)

    def delete_logs(self, records):
        paths = [self.file(record['id'], 'logs', path.name)
                 for record in records
                 for path in (self.folder(record['id'])/'logs').glob('heat_*.csv')]
        if not paths:
            return None, 0
        return self.move_to_trash(paths, None, 'all_logs'), len(paths)

    def purge_assay_trash(self, assay_ids=None):
        """Remove only trash belonging to explicitly deleted assay folders."""
        if self.trash.is_symlink() or self.assays.is_symlink():
            raise ValueError('Cannot purge linked archive folders')
        entries = []
        deleted = set(assay_ids or ())
        for folder in self.trash.iterdir():
            if folder.is_symlink() or not folder.is_dir() or not re.fullmatch(r'[0-9a-f]{32}',folder.name):
                continue
            path = folder/'manifest.json'
            if path.is_symlink() or not path.is_file():
                continue
            manifest = json.loads(path.read_text(encoding='utf-8'))
            aid = manifest.get('assay_id')
            if not isinstance(aid,str) or not re.fullmatch(r'\d{8}T\d{6}Z_[0-9a-f]{12}',aid):
                continue
            if manifest.get('restored_at') or (self.assays/aid).exists():
                continue
            entries.append((folder,aid))
            if manifest.get('kind') == 'assay':
                deleted.add(aid)
        count = size = 0
        for folder,aid in entries:
            if aid not in deleted:
                continue
            if folder.resolve().parent != self.trash.resolve():
                raise ValueError('Invalid trash folder')
            size += sum(p.stat().st_size for p in folder.rglob('*') if p.is_file() and not p.is_symlink())
            shutil.rmtree(folder)
            count += 1
        return dict(folders=count,bytes=size)

    def move_to_trash(self, paths, assay_id, kind):
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
        if manifest.get('kind') == 'assay':
            raise ValueError('Assay folder deletion is permanent')
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
        melting = self.validate_melting(data.get('melting'), gains)
        with self.lock:
            self.standalone()
            self.archive.assert_unique_name(data['name'])
            h,c=self.heater.snapshot(),self.camera.snapshot()
            if h['active'] or c['running'] or c['busy'] or c.get('pending'):
                raise ValueError('Stop independent heating and acquisition before starting an assay')
            if not c['ready']:
                raise ValueError('Camera not ready')
            self.camera.validate_reaction()
            record=self.archive.create(dict(name=data['name'].strip(),operator=data['operator'].strip(),
                target=data['target'],duration_minutes=data['duration_minutes'],interval_s=interval,
                expected_photos=count,pid=gains,started_at=utc_now(),ended_at=None,status='starting',reason=None,
                photo_layout='stages',stage='amplification',melting=melting,amplification_captured=0,
                melting_captured=0,melting_completed=False))
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

    def validate_melting(self, value, gains):
        if value is None or value is False:
            return None
        if not isinstance(value,dict):
            raise ValueError('Invalid melting settings')
        start,end,increment = (value.get(k) for k in ('start','end','increment'))
        if any(isinstance(v,bool) or not isinstance(v,(float,int)) or not math.isfinite(v)
               for v in (start,end,increment)):
            raise ValueError('Melting temperatures and increment must be finite numbers')
        if not 0 < start < end < self.c['cutoff_c'] or not 0.1 <= increment <= end-start:
            raise ValueError('Melting requires start < end below the safety cutoff; increment must be at least 0.1 °C and no larger than the range')
        steps = math.ceil(round((end-start)/increment,10))+1
        # Each point has a hard 90-second deadline: 60 seconds before capture,
        # followed by at most 30 seconds for illumination, capture and durable analysis.
        if steps*90 > self.c['max_duration_s']:
            raise ValueError('Too many melting points for the maximum heating duration')
        targets = [round(min(start+i*increment,end),6) for i in range(steps)]
        self.heater.validate(end,steps*90,gains)
        return dict(start=start,end=end,increment=increment,targets=targets,wait_s=60)

    def skip(self):
        with self.lock:
            if not self.active or self.active['stage'] != 'amplification' or not self.active['melting']:
                raise ValueError('Skip is available only during amplification with melting enabled')
            try:
                self.begin_transition(skipped=True)
            except BaseException as exc:
                self.finish('failed',str(exc))
                raise

    def begin_transition(self, skipped=False):
        record = self.active
        record.update(stage='transition',amplification_skipped=skipped,
                      amplification_captured=self.camera.snapshot()['captured'],amplification_ended_at=utc_now())
        self.camera.stop()
        self.heater.stop()
        self.deadline = self.clock()+30
        self.archive.save(record)

    def melting_step(self, index):
        record = self.active
        target = record['melting']['targets'][index]
        if index == 0:
            self.heater.start(target,len(record['melting']['targets'])*90,record['pid'],context=record,allow_recovery=True)
        else:
            self.heater.retarget(target)
        record.update(stage='melting',melting_index=index,melting_target=target)
        self.deadline = self.clock()+90
        self.capture_due = self.clock()+60
        self.camera.start(60,1,context=record,deadline=self.heater.deadline,
                          delay=60,continue_reaction=index>0)
        self.archive.save(record)

    def finish(self, status, reason=None):
        with self.lock:
            if not self.active:
                return
            try:
                self.heater.stop()
            finally:
                self.camera.stop()
            record=dict(self.active)
            count = self.camera.snapshot()['captured']
            if record.get('stage') == 'amplification':
                record['amplification_captured'] = count
            record.update(status=status,reason=reason,ended_at=utc_now(),
                          captured=record.get('amplification_captured',0)+record.get('melting_captured',0))
            self.active=None
            self.last=record
            self.archive.save(record)

    def tick(self):
        with self.lock:
            if not self.active:
                return
            h,c=self.heater.snapshot(),self.camera.snapshot()
            if h['fault'] or c['error'] or (not h['ready'] and not h.get('recovering')) or not c['ready']:
                self.finish('failed',h['fault'] or c['error'] or 'An instrument became unavailable')
            elif self.active['stage'] == 'transition':
                if self.clock() >= self.deadline:
                    self.finish('failed','Camera did not stop before melting')
                elif not c['busy'] and not c['running'] and not c.get('pending'):
                    self.melting_step(0)
            elif self.active['stage'] == 'melting':
                if self.clock() >= self.deadline or not h['active']:
                    self.finish('failed','Melting step timed out or heating stopped')
                elif not c['busy'] and not c['running'] and not c.get('pending'):
                    if c['captured'] != 1:
                        self.finish('failed','Melting photo was not recorded')
                    else:
                        record = self.active
                        record['melting_captured'] += 1
                        index = record['melting_index']+1
                        if index == len(record['melting']['targets']):
                            record['melting_completed'] = True
                            self.finish('completed')
                        else:
                            self.melting_step(index)
            elif self.clock() >= self.deadline:
                if self.active['melting']:
                    self.begin_transition()
                else:
                    self.finish('completed')
            elif not h['active']:
                # The heater timer is authoritative and starts just before the supervisor timer.
                if h['remaining_s']==0 and self.heater.clock()>=self.heater.deadline:
                    if self.active['melting']:
                        self.begin_transition()
                    else:
                        self.finish('completed')
                else:
                    self.finish('stopped','Heating stopped before the assay ended')

    def snapshot(self):
        with self.lock:
            remaining = max(0,self.deadline-self.clock()) if self.active else 0
            stage_remaining = remaining
            if self.active and self.active['stage'] == 'melting':
                remaining = max(0,self.capture_due-self.clock())
                later = len(self.active['melting']['targets'])-self.active['melting_index']-1
                stage_remaining = remaining+later*60+(later+1)*self.c.get('led_warmup_s',1)
            return dict(active=dict(self.active) if self.active else None,last=self.last,
                        remaining_s=remaining,stage_remaining_s=stage_remaining,error=self.error)

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
