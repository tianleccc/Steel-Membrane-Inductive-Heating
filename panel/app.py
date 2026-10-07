import json
import os
import re
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_file

from .assays import Assays, duration_seconds
from .camera import Camera
from .hardware import IRHardware
from .heating import Heater

ROOT = Path(__file__).resolve().parent.parent


def load_config():
    config = json.loads((ROOT/'config.example.json').read_text(encoding='utf-8'))
    path = Path(os.environ.get('PANEL_CONFIG', ROOT/'config.json'))
    if path.exists():
        config.update(json.loads(path.read_text(encoding='utf-8')))
    config['data_dir'] = str((ROOT/config['data_dir']).resolve())
    if config['heater_gpio'] == config['led_gpio']:
        raise ValueError('Heater and LED must use different GPIO pins')
    if not (0 < config['cycle_s'] <= 1 and 0 < config['off_window_s'] < config['cycle_s']
            and 1 <= config['sample_every'] <= 10
            and config['sample_every']*config['cycle_s'] <= 1
            and 1 <= config['samples'] <= 10 and 0 < config['ema'] <= 1
            and 0 < config['cutoff_c'] <= 110):
        raise ValueError('Invalid control timing, cutoff, or duty configuration')
    return config


def create_app(config=None, heater=None, camera=None):
    c = config or load_config()
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16384
    token = secrets.token_urlsafe(32)
    heater = heater or Heater(c, IRHardware)
    camera = camera or Camera(c, heater.snapshot)
    assays = Assays(c, heater, camera)
    archive = assays.archive
    app.extensions.update(heater=heater,camera=camera,assays=assays)

    @app.before_request
    def guard():
        if request.method == 'POST':
            # Same-origin page token protects physical controls against cross-site requests.
            origin = request.headers.get('Origin')
            if origin and urlsplit(origin).netloc != request.host:
                abort(403)
            if not secrets.compare_digest(request.headers.get('X-Panel-Token',''),token):
                abort(403)
            if not request.is_json:
                abort(415)

    @app.after_request
    def headers(response):
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['X-Frame-Options'] = 'DENY'
        response.headers['Referrer-Policy'] = 'same-origin'
        if request.path.startswith('/api') or request.path in ('/','/preview.jpg'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @app.errorhandler(ValueError)
    def bad_value(error):
        return jsonify(error=str(error)),400

    def body():
        data=request.get_json()
        if not isinstance(data,dict):
            raise ValueError('Invalid request format')
        return data

    @app.get('/')
    def index():
        return render_template('index.html',token=token,cutoff=c['cutoff_c'],
                               max_duration=c['max_duration_s']/60, pid={k:c[k] for k in ('kp','ki','kd')})

    @app.get('/api/status')
    def status():
        disk=shutil.disk_usage(c['data_dir'])
        return jsonify(heater=heater.snapshot(),camera=camera.snapshot(),assay=assays.snapshot(),
                       free_gb=round(disk.free/1e9,2))

    @app.get('/api/history')
    def history():
        with heater.lock:
            return jsonify(list(heater.history))

    @app.post('/api/heater/start')
    def heat_start():
        data=body()
        with assays.lock:
            assays.standalone()
            if 'duty_cap' in data or 'duration' in data:
                raise ValueError('This page is outdated. Refresh to use minutes and PID controls.')
            heater.start(data.get('target'),duration_seconds(data.get('duration_minutes'),c['max_duration_s']),data.get('pid'))
        return jsonify(ok=True)

    @app.post('/api/heater/stop')
    def heat_stop():
        with assays.lock:
            if assays.active:
                assays.finish('stopped','Heating stopped by operator')
            else:
                heater.stop()
        return jsonify(ok=True)

    @app.post('/api/heater/reset')
    def heat_reset():
        with assays.lock:
            assays.standalone()
            heater.clear_fault()
        return jsonify(ok=True)

    @app.post('/api/camera/start')
    def camera_start():
        data=body()
        with assays.lock:
            assays.standalone()
            camera.start(data.get('interval'),data.get('count'))
        return jsonify(ok=True)

    @app.post('/api/camera/capture')
    def capture():
        with assays.lock:
            assays.standalone()
            camera.capture()
        return jsonify(ok=True)

    @app.post('/api/camera/stop')
    def camera_stop():
        with assays.lock:
            if assays.active:
                assays.finish('stopped','Acquisition stopped by operator')
            else:
                camera.stop()
        return jsonify(ok=True)

    @app.post('/api/camera/light')
    def light():
        data=body()
        enabled=data.get('enabled')
        renew=data.get('renew',False)
        if not isinstance(enabled,bool) or not isinstance(renew,bool):
            raise ValueError('enabled and renew must be boolean values')
        with assays.lock:
            if enabled:
                assays.standalone()
            camera.light(enabled,renew=renew)
        return jsonify(ok=True)

    @app.post('/api/stop')
    def stop():
        with assays.lock:
            assays.finish('stopped','Stop all requested by operator')
            try:
                heater.stop()
            finally:
                camera.stop()
        return jsonify(ok=True)

    @app.get('/preview.jpg')
    def preview():
        import io,time
        with camera.lock:
            if camera.frame is None or time.monotonic()-camera.frame_at > 5:
                return '',503
            frame=camera.frame
        return send_file(io.BytesIO(frame),mimetype='image/jpeg')

    @app.post('/api/assays/start')
    def assay_start():
        return jsonify(assays.start(body()))

    @app.post('/api/assays/stop')
    def assay_stop():
        assays.finish('stopped','Stopped by operator')
        return jsonify(ok=True)

    @app.get('/api/assays')
    def assay_list():
        with assays.lock:
            records=archive.summaries()
        operator=request.args.get('operator','').strip().casefold()
        query=request.args.get('q','').strip().casefold()
        operators=sorted({r['operator'] for r in records if r['operator']},key=str.casefold)
        records=[r for r in records if (not operator or r['operator'].casefold()==operator)
                 and (not query or query in (r['name']+' '+r['operator']).casefold())]
        return jsonify(items=records,operators=operators)

    @app.get('/api/photos')
    def photos():
        page=max(0,int(request.args.get('page',0)))
        date=request.args.get('date','').replace('-','')
        if date and not re.fullmatch(r'\d{8}',date):
            raise ValueError('Invalid date format')
        assay_id=request.args.get('assay_id','unassigned')
        with assays.lock:
            folder=archive.folder(assay_id)/'photos'
            files=sorted((p for p in folder.glob(f'{date}*.jpg')
                          if not p.name.endswith('.thumb.jpg') and not p.is_symlink()),reverse=True)
            items=[]
            for path in files[page*24:page*24+24]:
                try:
                    metadata=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
                except (OSError,ValueError):
                    metadata={}
                items.append(dict(id=path.stem,utc=metadata.get('utc'),assay_id=assay_id,
                                  temperature=metadata.get('temperature',{}).get('temperature')))
        return jsonify(items=items,total=len(files),page=page)

    @app.get('/photos/<name>')
    def photo(name):
        path=archive.file(request.args.get('assay_id','unassigned'),'photos',name)
        return send_file(path,as_attachment=request.args.get('download')=='1')

    @app.get('/api/logs')
    def logs():
        assay_id=request.args.get('assay_id','')
        operator=request.args.get('operator','').strip().casefold()
        with assays.lock:
            records=archive.summaries()
            items=[]
            for record in records:
                if assay_id and record['id']!=assay_id:
                    continue
                if operator and record['operator'].casefold()!=operator:
                    continue
                for path in sorted((archive.folder(record['id'])/'logs').glob('heat_*.csv'),reverse=True):
                    if path.is_symlink():
                        continue
                    items.append(dict(name=path.name,assay_id=record['id'],assay_name=record['name'],
                                      operator=record['operator'],status=record['status']))
        return jsonify(items=items)

    @app.get('/logs/<name>')
    def log(name):
        return send_file(archive.file(request.args.get('assay_id','unassigned'),'logs',name),as_attachment=True)

    @app.post('/api/archive/delete')
    def delete():
        data=body()
        assay_id=data.get('assay_id','unassigned')
        with assays.lock:
            assays.assert_deletable(assay_id)
            trash_id=archive.delete(assay_id,data.get('kind'),data.get('name'))
        return jsonify(ok=True,trash_id=trash_id)

    @app.post('/api/archive/restore')
    def restore():
        with assays.lock:
            assays.standalone()
            # Do not restore into folders currently owned by standalone writers.
            assays.assert_deletable('unassigned')
            archive.restore(body().get('trash_id'))
        return jsonify(ok=True)

    return app
