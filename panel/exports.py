"""Background archive exports without blocking instrument supervision."""
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import threading
import uuid
import zipfile


def usb_drives():
    result = subprocess.run(['lsblk', '-J', '--tree', '-o', 'PATH,TRAN,MOUNTPOINTS,LABEL,RO'],
                            capture_output=True, text=True, check=True, timeout=5)
    drives = []
    def visit(device, usb=False):
        usb = usb or device.get('tran') == 'usb'
        if usb and not device.get('ro'):
            for mount in device.get('mountpoints') or []:
                if not mount or not mount.startswith(('/media/', '/run/media/', '/mnt/')):
                    continue
                path = Path(mount)
                if path.is_symlink() or not path.is_mount() or not os.access(path, os.W_OK):
                    continue
                stat = path.stat()
                drives.append(dict(id=device['path'], label=device.get('label') or path.name,
                    mount=str(path), free_bytes=shutil.disk_usage(path).free,
                    identity=(stat.st_dev, stat.st_ino)))
        for child in device.get('children', []):
            visit(child, usb)
    for device in json.loads(result.stdout)['blockdevices']:
        visit(device)
    return drives


class Exports:
    def __init__(self, assays, drives=usb_drives):
        self.assays, self.drives = assays, drives
        self.lock = threading.RLock()
        self.job = None
        self.cache = assays.archive.root / '.exports'
        self.cache.mkdir(exist_ok=True)
        # Only disposable ZIP exports live here; interrupted exports can be rebuilt.
        for path in self.cache.glob('*.zip'):
            if path.is_file() and not path.is_symlink():
                path.unlink()

    def protect(self, assay_id):
        with self.lock:
            if self.job and self.job['status'] == 'running' and self.job['assay_id'] == assay_id:
                raise ValueError('Wait for this folder export to finish before changing its files')

    def snapshot(self, job_id):
        with self.lock:
            if not self.job or self.job['id'] != job_id:
                raise ValueError('Export not found. Start a new export.')
            return {k:v for k,v in self.job.items() if not k.startswith('_')}

    def start(self, assay_id, mode, drive_id=None):
        if mode not in ('photos', 'usb'):
            raise ValueError('Invalid export mode')
        with self.assays.lock, self.lock:
            if self.job and self.job['status'] == 'running':
                raise ValueError('An export is already in progress')
            self.assays.assert_deletable(assay_id)
            folder = self.assays.archive.folder(assay_id)
            files = []
            for kind, pattern in [('photos', '*'), ('logs', '*.csv')]:
                if mode == 'photos' and kind == 'logs':
                    continue
                if (folder/kind).is_symlink():
                    raise ValueError('Cannot export linked folders')
                for path in sorted((folder/kind).glob(pattern)):
                    if path.is_symlink():
                        raise ValueError('Cannot export linked files')
                    if path.is_file() and path.suffix in ('.jpg', '.json', '.csv'):
                        if mode == 'photos' and path.name.endswith('.thumb.jpg'):
                            continue
                        files.append((path, path.relative_to(folder)))
            manifest = folder/'assay.json'
            name = assay_id
            if assay_id != 'unassigned':
                if manifest.is_symlink():
                    raise ValueError('Cannot export a linked manifest')
                record = json.loads(manifest.read_text(encoding='utf-8'))
                name = re.sub(r'[^A-Za-z0-9_-]+', '_', record['name']).strip('_')[:60] or 'Assay'
                name += '_' + assay_id
                files.append((manifest, Path('assay.json')))
            if not any(path.suffix == '.jpg' and not path.name.endswith('.thumb.jpg') for path,_ in files) and mode == 'photos':
                raise ValueError('This folder has no photos')
            if not files:
                raise ValueError('This folder is empty')
            drive = None
            if mode == 'usb':
                drive = next((d for d in self.drives() if d['id'] == drive_id), None)
                if not drive:
                    raise ValueError('USB drive not available. Insert it and open it in the Pi file manager, then refresh.')
            total = sum(path.stat().st_size for path,_ in files)
            free = drive['free_bytes'] if drive else shutil.disk_usage(self.cache).free
            if free < total + 64*1024*1024:
                raise ValueError('Not enough free space for this export (64 MB reserve required)')
            if self.job and self.job.get('_zip'):
                self.job['_zip'].unlink(missing_ok=True)
            job = dict(id=uuid.uuid4().hex, assay_id=assay_id, mode=mode, status='running',
                       completed=0, total=len(files), bytes=total, name=name, error=None)
            self.job = job
            threading.Thread(target=self.run, args=(job,files,drive), daemon=True, name='archive-export').start()
            return self.snapshot(job['id'])

    def run(self, job, files, drive):
        fd = None
        partial = None
        try:
            if drive:
                # Keep a directory descriptor on the mounted filesystem. Unplugging must
                # never redirect writes into the Pi's underlying /media directory.
                fd = os.open(drive['mount'], os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                stat = os.fstat(fd)
                if (stat.st_dev, stat.st_ino) != tuple(drive['identity']):
                    raise ValueError('USB drive changed. Refresh the drive list and try again.')
                partial = '.steel-export-' + job['id'] + '.partial'
                os.mkdir(partial, dir_fd=fd)
                target = Path(f'/proc/self/fd/{fd}')/partial
                for source, relative in files:
                    destination = target/relative
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, destination)
                    with destination.open('rb') as saved:
                        os.fsync(saved.fileno())
                    if source.stat().st_size != destination.stat().st_size:
                        raise OSError('Export size verification failed')
                    with self.lock:
                        job['completed'] += 1
                for directory in [target/'photos', target/'logs', target]:
                    if directory.is_dir():
                        d = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
                        try:
                            os.fsync(d)
                        finally:
                            os.close(d)
                final = job['name'] + '_' + job['id'][:8]
                os.rename(partial, final, src_dir_fd=fd, dst_dir_fd=fd)
                os.fsync(fd)
                job['destination'] = drive['mount'] + '/' + final
            else:
                target = self.cache/(job['id']+'.zip')
                with zipfile.ZipFile(target, 'w', compression=zipfile.ZIP_STORED, allowZip64=True) as bundle:
                    for source, relative in files:
                        bundle.write(source, str(Path(job['name'])/relative))
                        with self.lock:
                            job['completed'] += 1
                job['_zip'] = target
            with self.lock:
                job['status'] = 'complete'
        except Exception as error:
            with self.lock:
                job.update(status='failed', error=str(error) +
                    (' An incomplete .partial folder may remain on the USB; the original assay is unchanged.' if drive else ''))
            if not drive:
                (self.cache/(job['id']+'.zip')).unlink(missing_ok=True)
        finally:
            if fd is not None:
                os.close(fd)

    def download(self, job_id):
        with self.lock:
            self.snapshot(job_id)
            if self.job['status'] != 'complete' or not self.job.get('_zip'):
                raise ValueError('ZIP is not ready')
            return self.job['_zip'].open('rb'), self.job['name']+'.zip'
