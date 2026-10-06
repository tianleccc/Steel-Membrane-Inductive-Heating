import os
os.environ.setdefault('GPIOZERO_PIN_FACTORY','lgpio')
import fcntl
import signal
from pathlib import Path
from waitress import serve
from panel.app import create_app,load_config


def main():
    config=load_config()
    Path(config['data_dir']).mkdir(parents=True,exist_ok=True)
    lock=(Path(config['data_dir'])/'panel.lock').open('w')
    fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    app=create_app(config)
    heater,camera=app.extensions['heater'],app.extensions['camera']
    def shutdown(*_):
        raise SystemExit(0)
    signal.signal(signal.SIGTERM,shutdown)
    signal.signal(signal.SIGINT,shutdown)
    try:
        heater.launch()
        camera.launch()
        serve(app,host=config['host'],port=config['port'],threads=8)
    finally:
        try:
            heater.close()
        finally:
            camera.close()
            lock.close()


if __name__=='__main__':
    main()
