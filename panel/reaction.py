"""Causal fluorescence measurements, stored with each original photograph.

The photo and its CSV are one archive item. Download/export combines the CSV
rows without recalculating historical EMA values after a deletion or restart.
"""
import csv
import io
import json
import math
import os
import uuid
from pathlib import Path

ALPHA = 0.35
HSV_LOW = (35, 50, 50)
HSV_HIGH = (85, 255, 255)
METHOD = 'green-hsv-v-sum-x2_ema-running-min_v1'
REGION_FILE = Path(__file__).with_name('reaction_regions.json')
FIELDS = ['Image', 'UTC', 'Assay_ID', 'Reaction_ID', 'Time_min', 'Method',
          'EMA_alpha', 'HSV_low', 'HSV_high', 'Width_px', 'Height_px']
for region_id in range(1, 5):
    FIELDS += [f'{field}_Region_{region_id}' for field in
               ('Center_X_px', 'Center_Y_px', 'Radius_px', 'Raw', 'Smoothed', 'Background', 'Corrected')]
LEGACY_FIELDS = list(FIELDS)
FIELDS += ['Stage', 'Setpoint_C', 'Measured_C']
STAGE_FIELDS = list(FIELDS)
FIELDS += ['Temperature_status']


def photo_folders(folder):
    folders = [folder/'photos', folder/'photos'/'amplification', folder/'photos'/'melting']
    if any(p.is_symlink() for p in folders):
        raise ValueError('Cannot read linked photo folders')
    return folders


def photo_files(folder):
    return sorted(p for directory in photo_folders(folder) for p in directory.glob('*.jpg')
                  if not p.name.endswith('.thumb.jpg'))


def settings():
    data = json.loads(REGION_FILE.read_text(encoding='utf-8'))
    return dict(data, alpha=ALPHA, hsv_low=HSV_LOW, hsv_high=HSV_HIGH, method=METHOD)


class ReactionSession:
    def __init__(self, config):
        self.settings = settings()
        if [config['photo_width'], config['photo_height']] != self.settings['image_size']:
            raise ValueError('Reaction regions require 4608 × 2592 photos. Check camera resolution.')
        # Fail before starting acquisition/heating if the dependency is missing.
        try:
            import cv2
            import numpy as np
        except ImportError as error:
            raise ValueError('Reaction analysis needs python3-opencv. Run the updated installer.') from error
        self.cv2, self.np = cv2, np
        self.id = uuid.uuid4().hex
        self.first_time = None
        self.smooth = self.background = None

    def analyze(self, path, image_name, utc, assay_id, elapsed_s):
        cv2, np = self.cv2, self.np
        # Analyze the saved JPEG, exactly as the supplied offline analyzer does.
        image = cv2.imread(str(path))
        if image is None or list(image.shape[1::-1]) != self.settings['image_size']:
            raise ValueError('Reaction image is unreadable or has the wrong dimensions')
        raw = []
        for region in self.settings['coordinates']:
            x, y = (round(v) for v in region['center'])
            radius = round(region['radius'])
            # HSV conversion is pixel-local: crop each ROI to avoid full-frame masks.
            crop = image[y-radius:y+radius+1, x-radius:x+radius+1]
            hsv = cv2.cvtColor(crop, cv2.COLOR_BGR2HSV)
            green = cv2.inRange(hsv, np.array(HSV_LOW), np.array(HSV_HIGH))
            circle = np.zeros(green.shape, np.uint8)
            cv2.circle(circle, (radius, radius), radius, 255, -1)
            values = hsv[:, :, 2][(green == 255) & (circle == 255)]
            raw.append(int(np.sum(values, dtype=np.int64) * 2))
        smooth = raw if self.smooth is None else [ALPHA*r+(1-ALPHA)*s for r,s in zip(raw,self.smooth)]
        background = smooth if self.background is None else [min(b,s) for b,s in zip(self.background,smooth)]
        if self.first_time is None:
            self.first_time = elapsed_s
        row = dict(Image=image_name, UTC=utc, Assay_ID=assay_id or 'unassigned',
                   Reaction_ID=self.id, Time_min=max(0, elapsed_s-self.first_time)/60,
                   Stage='amplification', Setpoint_C='', Measured_C='', Temperature_status='unavailable',
                   Method=METHOD, EMA_alpha=ALPHA, HSV_low=','.join(map(str,HSV_LOW)),
                   HSV_high=','.join(map(str,HSV_HIGH)), Width_px=image.shape[1], Height_px=image.shape[0])
        for i, region in enumerate(self.settings['coordinates']):
            values = [region['center'][0], region['center'][1], region['radius'],
                      raw[i], smooth[i], background[i], max(0, smooth[i]-background[i])]
            for key, value in zip(('Center_X_px','Center_Y_px','Radius_px','Raw','Smoothed','Background','Corrected'),values):
                row[f'{key}_Region_{i+1}'] = value
        return row, smooth, background

    def commit(self, measurement):
        _, self.smooth, self.background = measurement


def write_measurement(path, row):
    """Durably prepare the CSV before publishing the matching JPEG."""
    with path.open('w', newline='', encoding='utf-8') as stream:
        writer = csv.DictWriter(stream, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerow(row)
        stream.flush()
        os.fsync(stream.fileno())


def read_rows(folder, stage=None):
    rows = []
    for path in sorted(p for photos in photo_folders(folder) for p in photos.glob('*.reaction.csv')):
        photo = path.with_name(path.name.removesuffix('.reaction.csv')+'.jpg')
        if path.is_symlink() or photo.is_symlink():
            raise ValueError('Cannot read linked reaction files')
        if not photo.is_file():
            continue  # A pending/incomplete capture is not a published measurement.
        with path.open(newline='', encoding='utf-8') as stream:
            reader = csv.DictReader(stream)
            if reader.fieldnames not in (FIELDS, STAGE_FIELDS, LEGACY_FIELDS):
                raise ValueError(f'Invalid reaction log: {path.name}')
            data = list(reader)
        if len(data) != 1 or data[0]['Image'] != photo.name:
            raise ValueError(f'Reaction log does not match its photo: {path.name}')
        row = data[0]
        row.setdefault('Stage', 'amplification')
        row.setdefault('Setpoint_C', '')
        row.setdefault('Measured_C', '')
        row.setdefault('Temperature_status', 'valid' if row['Measured_C'] != '' else 'unavailable')
        if row['Stage'] not in ('amplification', 'melting'):
            raise ValueError('Invalid reaction stage')
        for key in ['Time_min', 'EMA_alpha', 'Width_px', 'Height_px'] + LEGACY_FIELDS[11:]:
            row[key] = float(row[key])
            if not math.isfinite(row[key]):
                raise ValueError(f'Invalid reaction value: {path.name}')
        for key in ('Setpoint_C', 'Measured_C'):
            if row[key] != '':
                row[key] = float(row[key])
                if not math.isfinite(row[key]):
                    raise ValueError('Invalid reaction temperature')
        if stage is None or row['Stage'] == stage:
            rows.append(row)
    return sorted(rows, key=lambda r: r['Image'])


def csv_text(rows, export_names=None):
    output = io.StringIO(newline='')
    writer = csv.DictWriter(output, fieldnames=FIELDS+(['Export_image'] if export_names is not None else []))
    writer.writeheader()
    for row in rows:
        if export_names is not None:
            row = dict(row, Export_image=export_names[row['Image']])
        writer.writerow(row)
    return output.getvalue()


def series(rows, session_id=None):
    sessions = {}
    for row in rows:
        entry = sessions.setdefault(row['Reaction_ID'], dict(id=row['Reaction_ID'], utc=row['UTC'], count=0))
        entry['count'] += 1
    chosen = session_id or (rows[-1]['Reaction_ID'] if rows else None)
    if session_id and session_id not in sessions:
        raise ValueError('Reaction run not found in this folder')
    points = [dict(photo=row['Image'], utc=row['UTC'], time_min=row['Time_min'],
                   raw=[row[f'Raw_Region_{i}'] for i in range(1,5)],
                   corrected=[row[f'Corrected_Region_{i}'] for i in range(1,5)])
              for row in rows if row['Reaction_ID'] == chosen]
    return dict(sessions=list(reversed(list(sessions.values()))), session_id=chosen, points=points)


def melting_series(rows, completed=False):
    points = [dict(photo=r['Image'], utc=r['UTC'], time_min=r['Time_min'],
                   temperature=r['Setpoint_C'], measured_c=r['Measured_C'],
                   raw=[r[f'Raw_Region_{i}'] for i in range(1,5)])
              for r in rows if r['Stage'] == 'melting' and r['Setpoint_C'] != '']
    # Local quadratic least squares on actual setpoints (also handles a short
    # final increment). Five observations, centered inside, one-sided at edges.
    # Keep short traces unchanged; never replace the immutable raw CSV values.
    if len(points) >= 5:
        import numpy as np
        temperatures = np.array([p['temperature'] for p in points])
        if np.any(np.diff(temperatures) <= 0):
            raise ValueError('Melting temperatures must increase')
        raw = np.array([p['raw'] for p in points])
        for j,point in enumerate(points):
            left = min(max(0,j-2),len(points)-5)
            x = temperatures[left:left+5]-temperatures[j]
            x = x/max(abs(x))
            coefficients = np.linalg.lstsq(np.column_stack([np.ones(5),x,x*x]),raw[left:left+5],rcond=None)[0]
            point['smoothed'] = coefficients[0].tolist()
    else:
        for point in points:
            point['smoothed'] = list(point['raw'])
    derivative = []
    if completed and len(points) >= 2:
        for j, point in enumerate(points):
            left, right = points[max(0,j-1)], points[min(len(points)-1,j+1)]
            span = right['temperature']-left['temperature']
            if span <= 0:
                raise ValueError('Melting temperatures must increase')
            # One-sided differences at endpoints, nonuniform three-point gradient inside.
            values = [(a-b)/span for a,b in zip(left['smoothed'],right['smoothed'])]
            if 0 < j < len(points)-1:
                a = point['temperature']-left['temperature']
                b = right['temperature']-point['temperature']
                if a <= 0 or b <= 0:
                    raise ValueError('Melting temperatures must increase')
                values = [(b*(l-m)/a+a*(m-r)/b)/(a+b)
                          for l,m,r in zip(left['smoothed'],point['smoothed'],right['smoothed'])]
            derivative.append(dict(temperature=point['temperature'], derivative=values))
    return dict(points=points, derivative=derivative, completed=completed)
