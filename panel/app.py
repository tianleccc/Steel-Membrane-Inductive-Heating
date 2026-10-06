import json
import os
import re
import secrets
import shutil
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, abort, jsonify, render_template, request, send_file, send_from_directory

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
            and 0 < config['duty_cap'] <= 100 and 0 < config['cutoff_c'] <= 110):
        raise ValueError('Invalid control timing, cutoff, or duty configuration')
    return config


def create_app(config=None, heater=None, camera=None):
    c = config or load_config()
    app = Flask(__name__)
    app.config['MAX_CONTENT_LENGTH'] = 16384
    token = secrets.token_urlsafe(32)
    heater = heater or Heater(c, IRHardware)
    camera = camera or Camera(c, heater.snapshot)
    app.extensions.update(heater=heater,camera=camera)

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
            raise ValueError('请求格式错误')
        return data

    @app.get('/')
    def index():
        return render_template('index.html',token=token,cutoff=c['cutoff_c'],
                               max_duration=c['max_duration_s'],max_duty=c['duty_cap'])

    @app.get('/api/status')
    def status():
        disk=shutil.disk_usage(c['data_dir'])
        return jsonify(heater=heater.snapshot(),camera=camera.snapshot(),free_gb=round(disk.free/1e9,2))

    @app.get('/api/history')
    def history():
        with heater.lock:
            return jsonify(list(heater.history))

    @app.post('/api/heater/start')
    def heat_start():
        data=body()
        heater.start(data.get('target'),data.get('duration'),data.get('duty_cap'))
        return jsonify(ok=True)

    @app.post('/api/heater/stop')
    def heat_stop():
        heater.stop()
        return jsonify(ok=True)

    @app.post('/api/heater/reset')
    def heat_reset():
        heater.clear_fault()
        return jsonify(ok=True)

    @app.post('/api/camera/start')
    def camera_start():
        data=body()
        camera.start(data.get('interval'),data.get('count'))
        return jsonify(ok=True)

    @app.post('/api/camera/capture')
    def capture():
        camera.capture()
        return jsonify(ok=True)

    @app.post('/api/camera/stop')
    def camera_stop():
        camera.stop()
        return jsonify(ok=True)

    @app.post('/api/camera/light')
    def light():
        enabled=body().get('enabled')
        if not isinstance(enabled,bool):
            raise ValueError('enabled 必须为布尔值')
        camera.light(enabled)
        return jsonify(ok=True)

    @app.post('/api/stop')
    def stop():
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

    @app.get('/api/photos')
    def photos():
        page=max(0,int(request.args.get('page',0)))
        date=request.args.get('date','').replace('-','')
        if date and not re.fullmatch(r'\d{8}',date):
            raise ValueError('日期格式错误')
        files=sorted((p for p in camera.photos.glob(f'{date}*.jpg')
                      if not p.name.endswith('.thumb.jpg')),reverse=True)
        items=[]
        for path in files[page*24:page*24+24]:
            try:
                metadata=json.loads(path.with_suffix('.json').read_text(encoding='utf-8'))
            except (OSError,ValueError):
                metadata={}
            items.append(dict(id=path.stem,utc=metadata.get('utc'),
                              temperature=metadata.get('temperature',{}).get('temperature')))
        return jsonify(items=items,total=len(files),page=page)

    @app.get('/photos/<name>')
    def photo(name):
        if not re.fullmatch(r'\d{8}T\d{6}_\d{6}Z(?:\.thumb)?\.jpg',name):
            abort(404)
        return send_from_directory(camera.photos,name,as_attachment=request.args.get('download')=='1')

    @app.get('/api/logs')
    def logs():
        folder=Path(c['data_dir'])/'logs'
        return jsonify([p.name for p in sorted(folder.glob('heat_*.csv'),reverse=True)])

    @app.get('/logs/<name>')
    def log(name):
        if not re.fullmatch(r'heat_\d{8}T\d{6}_\d{6}Z\.csv',name):
            abort(404)
        return send_from_directory(Path(c['data_dir'])/'logs',name,as_attachment=True)

    return app
