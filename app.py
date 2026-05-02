"""
app.py
------
Main Flask application for the Smart Parking System.
Provides API endpoints for slot management, reservations,
dashboard data, and predictions.
"""

import os
import io
import ssl
import smtplib
import logging
import threading
import time
from datetime import datetime, timedelta
from email.mime.application import MIMEApplication
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from flask import Flask, render_template, request, jsonify, Response, send_file
from flask_cors import CORS
import cv2
import numpy as np
import mysql.connector
from mysql.connector import Error
import qrcode

# ReportLab imports for PDF generation
from reportlab.lib.pagesizes import A5
from reportlab.lib import colors
from reportlab.lib.units import mm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.platypus import Image as RLImage
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.enums import TA_CENTER, TA_LEFT

from detection.vehicle_detection import VehicleDetector
from prediction.forecasting import ParkingForecaster

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
app = Flask(__name__)
CORS(app)

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s  %(levelname)-8s  %(name)s  %(message)s')
logger = logging.getLogger(__name__)

DB_CONFIG = {
    'host': os.getenv('DB_HOST', 'localhost'),
    'user': os.getenv('DB_USER', 'root'),
    'password': os.getenv('DB_PASSWORD', 'root'),
    'database': os.getenv('DB_NAME', 'smart_parking'),
}

TOTAL_SLOTS = 68
VIDEO_SOURCE = os.getenv('VIDEO_SOURCE', 'carPark.mp4')  # video file path or camera index
DETECTION_INTERVAL = int(os.getenv('DETECTION_INTERVAL', '30'))  # seconds between auto-scans
GRACE_PERIOD_MINUTES = 15  # minutes to wait for car to arrive after booking
GRACE_WARNING_MINUTES = 5  # send warning email when this many minutes remain
END_REMINDER_MINUTES = 10  # remind user this many minutes before reservation ends

# Mail configuration — env vars take priority; hardcoded values are the fallback
MAIL_USER     = os.getenv('MAIL_USER', '')
MAIL_PASSWORD = os.getenv('MAIL_PASSWORD', '')

# App host for QR code URLs (used in PDF QR codes)
APP_HOST = os.getenv('APP_HOST', 'http://localhost:5000')

# Initialise modules
detector = VehicleDetector()
forecaster = ParkingForecaster(total_slots=TOTAL_SLOTS)


# ---------------------------------------------------------------------------
# Database helpers
# ---------------------------------------------------------------------------
def get_db():
    """Create and return a MySQL database connection."""
    try:
        conn = mysql.connector.connect(**DB_CONFIG)
        return conn
    except Error as e:
        logger.error(f"Database connection error: {e}")
        return None


def query_db(sql, params=None, fetchone=False, commit=False):
    """Execute a query and return results."""
    conn = get_db()
    if conn is None:
        return None
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params or ())
        if commit:
            conn.commit()
            return cursor.lastrowid
        return cursor.fetchone() if fetchone else cursor.fetchall()
    except Error as e:
        logger.error(f"Query error: {e}")
        return None
    finally:
        conn.close()


# ---------------------------------------------------------------------------
# Page routes
# ---------------------------------------------------------------------------
@app.route('/')
def index():
    """Render the main parking view page."""
    return render_template('index.html')


@app.route('/dashboard')
def dashboard():
    """Render the admin dashboard page."""
    return render_template('dashboard.html')


@app.route('/reserve-page')
def reserve_page():
    """Render the dedicated slot reservation page."""
    return render_template('reserve.html')


# ---------------------------------------------------------------------------
# API  –  GET /video-feed  (MJPEG stream)
# ---------------------------------------------------------------------------
def _generate_video_feed():
    """Generator that yields annotated MJPEG frames in a loop."""
    while True:
        source = int(VIDEO_SOURCE) if VIDEO_SOURCE.isdigit() else VIDEO_SOURCE
        cap = cv2.VideoCapture(source)

        if not cap.isOpened():
            logger.error("Video feed: cannot open source")
            # Yield a blank frame so the stream doesn't die
            blank = 255 * np.ones((480, 640, 3), dtype=np.uint8)
            cv2.putText(blank, 'No video source', (150, 240),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 200), 2)
            _, buf = cv2.imencode('.jpg', blank)
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buf.tobytes() + b'\r\n')
            time.sleep(2)
            continue

        # Get slot coordinates from DB
        slots = query_db("SELECT slot_id, slot_label, x1, y1, x2, y2 FROM parking_slots") or []

        _feed_last_db_write = 0.0   # timestamp of last DB update from feed

        while True:
            ret, frame = cap.read()
            if not ret:
                # Loop video from the start
                cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                break

            # Run detection on the frame
            slot_results = detector.process_frame(frame, slots)

            # ── Push results to DB every 3 s (same frame the user sees) ──────
            now_ts = time.time()
            if now_ts - _feed_last_db_write >= 3.0 and slot_results:
                grace_map = _get_grace_reservations()
                for sid, result in slot_results.items():
                    if sid in grace_map and not result['is_occupied']:
                        # Only skip if car never arrived (DB also shows unoccupied)
                        db_row = query_db(
                            "SELECT is_occupied FROM parking_slots WHERE slot_id = %s",
                            (sid,), fetchone=True
                        )
                        if db_row and not db_row['is_occupied']:
                            continue
                    query_db(
                        "UPDATE parking_slots SET is_occupied = %s WHERE slot_id = %s",
                        (result['is_occupied'], sid), commit=True
                    )
                _check_expired_grace()
                _feed_last_db_write = now_ts
            # ─────────────────────────────────────────────────────────────────

            # Draw slot overlays
            overlay = frame.copy()
            for s in slots:
                sid = s['slot_id']
                x1, y1, x2, y2 = s['x1'], s['y1'], s['x2'], s['y2']
                occupied = slot_results.get(sid, {}).get('is_occupied', False)

                color = (0, 0, 200) if occupied else (0, 200, 0)  # Red / Green
                cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)

                label = s.get('slot_label', f'S{sid}')
                px = slot_results.get(sid, {}).get('pixel_count', '')
                text = f"{label} {px}" if px != '' else label
                (tw, th), _ = cv2.getTextSize(text, cv2.FONT_HERSHEY_SIMPLEX, 0.35, 1)
                cx = (x1 + x2) // 2 - tw // 2
                cy = (y1 + y2) // 2 + th // 2
                cv2.putText(overlay, text, (cx, cy),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.35, (255, 255, 255), 1)

            # Blend overlay at 40% opacity
            frame = cv2.addWeighted(overlay, 0.4, frame, 0.6, 0)

            # Stats bar
            occ = sum(1 for r in slot_results.values() if r.get('is_occupied'))
            free = len(slots) - occ
            cv2.putText(frame, f'Occupied: {occ}  |  Free: {free}', (10, 25),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255, 255, 255), 2)

            # Encode and yield
            _, buffer = cv2.imencode('.jpg', frame, [cv2.IMWRITE_JPEG_QUALITY, 70])
            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

            time.sleep(0.1)  # ~10 FPS

        cap.release()


@app.route('/video-feed')
def video_feed():
    """Stream annotated parking video as MJPEG."""
    return Response(_generate_video_feed(),
                    mimetype='multipart/x-mixed-replace; boundary=frame')


# ---------------------------------------------------------------------------
# API  –  GET /slots
# ---------------------------------------------------------------------------
@app.route('/slots', methods=['GET'])
def get_slots():
    """
    Return the current status of all parking slots.
    Optionally triggers a fresh detection scan.
    """
    try:
        # Check and cancel expired grace-period reservations first
        _check_expired_grace()

        slots = query_db("SELECT * FROM parking_slots ORDER BY slot_id")
        if slots is None:
            slots = _demo_slots()

        # Augment each slot with reservation / grace info
        grace_map = _get_grace_reservations()
        for s in slots:
            sid = s['slot_id']
            if sid in grace_map:
                g = grace_map[sid]
                s['reservation_status'] = 'grace'
                s['reserved_by'] = g['user_name']
                s['grace_expires'] = g['grace_expires'].strftime('%Y-%m-%d %H:%M')
                s['grace_minutes_left'] = g['minutes_left']
            elif s.get('is_occupied'):
                s['reservation_status'] = 'occupied'
            else:
                s['reservation_status'] = 'available'

        occupied_or_reserved = sum(1 for s in slots if s['reservation_status'] != 'available')
        return jsonify({
            'success': True,
            'total_slots': len(slots),
            'occupied': occupied_or_reserved,
            'available': len(slots) - occupied_or_reserved,
            'slots': slots
        })
    except Exception as e:
        logger.error(f"/slots error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  POST /reserve
# ---------------------------------------------------------------------------
@app.route('/reserve', methods=['POST'])
def reserve_slot():
    """
    Book a parking slot for a user.
    Expects JSON: { user_name, car_plate, user_email, slot_id, start_time, end_time }
    """
    data = request.get_json(force=True)
    required_fields = ['user_name', 'slot_id', 'start_time', 'end_time']
    missing = [f for f in required_fields if f not in data]
    if missing:
        return jsonify({'success': False, 'error': f'Missing fields: {missing}'}), 400

    try:
        user_name  = data['user_name'].strip()
        car_plate  = data.get('car_plate', '').strip().upper()
        user_email = data.get('user_email', '').strip()
        slot_id    = int(data['slot_id'])
        start_time = datetime.strptime(data['start_time'], '%Y-%m-%dT%H:%M')
        end_time   = datetime.strptime(data['end_time'], '%Y-%m-%dT%H:%M')

        if end_time <= start_time:
            return jsonify({'success': False, 'error': 'End time must be after start time.'}), 400

        # Check for double-booking
        conflict = query_db(
            """SELECT reservation_id FROM reservations
               WHERE slot_id = %s AND status IN ('active','confirmed')
               AND start_time < %s AND end_time > %s""",
            (slot_id, end_time, start_time),
            fetchone=True
        )
        if conflict:
            return jsonify({'success': False, 'error': 'Slot is already booked for that period.'}), 409

        # Fetch slot label for the email / PDF
        slot_row = query_db(
            "SELECT slot_label FROM parking_slots WHERE slot_id = %s",
            (slot_id,), fetchone=True
        )
        slot_label = slot_row['slot_label'] if slot_row else f'Slot {slot_id}'

        # Insert reservation (with car_plate and user_email)
        res_id = query_db(
            """INSERT INTO reservations
                   (user_name, car_plate, user_email, slot_id, start_time, end_time, status)
               VALUES (%s, %s, %s, %s, %s, %s, 'active')""",
            (user_name, car_plate, user_email, slot_id, start_time, end_time),
            commit=True
        )

        # Send confirmation email with PDF token in a background thread
        if user_email and MAIL_USER and MAIL_PASSWORD:
            reservation_info = {
                'reservation_id': res_id,
                'user_name': user_name,
                'car_plate': car_plate,
                'user_email': user_email,
                'slot_label': slot_label,
                'start_time': start_time,
                'end_time': end_time,
            }
            t = threading.Thread(
                target=send_reservation_email,
                args=(reservation_info,),
                daemon=True
            )
            t.start()

        return jsonify({
            'success': True,
            'message': f'Slot {slot_label} reserved for {user_name}. Confirmation email sent!',
            'reservation_id': res_id
        }), 201

    except Exception as e:
        logger.error(f"/reserve error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  GET /reservation-pdf/<reservation_id>  (re-download token PDF)
# ---------------------------------------------------------------------------
@app.route('/reservation-pdf/<int:reservation_id>', methods=['GET'])
def get_reservation_pdf(reservation_id):
    """Re-generate and stream the parking token PDF for a given reservation."""
    try:
        res = query_db(
            """SELECT r.*, p.slot_label
               FROM reservations r
               JOIN parking_slots p ON r.slot_id = p.slot_id
               WHERE r.reservation_id = %s""",
            (reservation_id,), fetchone=True
        )
        if not res:
            return jsonify({'success': False, 'error': 'Reservation not found.'}), 404

        reservation_info = {
            'reservation_id': res['reservation_id'],
            'user_name': res['user_name'],
            'car_plate': res.get('car_plate') or 'N/A',
            'user_email': res.get('user_email') or '',
            'slot_label': res['slot_label'],
            'start_time': res['start_time'] if isinstance(res['start_time'], datetime) else datetime.strptime(str(res['start_time']), '%Y-%m-%d %H:%M:%S'),
            'end_time':   res['end_time']   if isinstance(res['end_time'],   datetime) else datetime.strptime(str(res['end_time']),   '%Y-%m-%d %H:%M:%S'),
        }
        pdf_bytes = _build_reservation_pdf(reservation_info)
        return send_file(
            io.BytesIO(pdf_bytes),
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'parking_token_{reservation_id}.pdf'
        )
    except Exception as e:
        logger.error(f"/reservation-pdf error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  GET /dashboard-data
# ---------------------------------------------------------------------------
@app.route('/dashboard-data', methods=['GET'])
def dashboard_data():
    """
    Returns aggregated data for the admin dashboard:
      - Slot summary
      - Reservations list
      - Overstay alerts
      - Peak-hour analysis
      - Daily occupancy history
    """
    try:
        # Check expired grace periods first
        _check_expired_grace()

        # --- Slot summary ---
        slots = query_db("SELECT * FROM parking_slots ORDER BY slot_id") or _demo_slots()
        grace_map = _get_grace_reservations()

        # Augment slots with grace info (same logic as /slots)
        for s in slots:
            sid = s['slot_id']
            if sid in grace_map:
                g = grace_map[sid]
                s['reservation_status'] = 'grace'
                s['reserved_by'] = g['user_name']
                s['grace_expires'] = g['grace_expires'].strftime('%Y-%m-%d %H:%M')
                s['grace_minutes_left'] = g['minutes_left']
            elif s.get('is_occupied'):
                s['reservation_status'] = 'occupied'
            else:
                s['reservation_status'] = 'available'

        total = len(slots)
        occupied_or_reserved = sum(1 for s in slots if s['reservation_status'] != 'available')
        available = total - occupied_or_reserved

        # --- Active reservations ---
        reservations = query_db(
            """SELECT r.*, p.slot_label
               FROM reservations r
               JOIN parking_slots p ON r.slot_id = p.slot_id
               WHERE r.status = 'active'
               ORDER BY r.start_time DESC"""
        ) or []

        # --- Confirmed reservations ---
        confirmed_reservations = query_db(
            """SELECT r.*, p.slot_label
               FROM reservations r
               JOIN parking_slots p ON r.slot_id = p.slot_id
               WHERE r.status = 'confirmed'
               ORDER BY r.start_time DESC"""
        ) or []

        # --- Detect overstays ---
        now = datetime.now()
        overstays = []
        for res in reservations:
            end_t = res.get('end_time')
            if end_t and isinstance(end_t, datetime) and end_t < now:
                overstays.append({
                    'reservation_id': res['reservation_id'],
                    'user_name': res['user_name'],
                    'slot_label': res.get('slot_label', f"Slot {res['slot_id']}"),
                    'expected_end': end_t.strftime('%Y-%m-%d %H:%M'),
                    'overstay_minutes': int((now - end_t).total_seconds() / 60)
                })
                # Log overstay violation
                _log_overstay(res)

        # --- Occupancy history (last 7 days) ---
        history = query_db(
            """SELECT * FROM occupancy_history
               ORDER BY timestamp DESC LIMIT 168"""
        ) or _demo_history()

        # --- Peak hours ---
        history_for_peaks = query_db(
            "SELECT hour_of_day, occupied_slots FROM occupancy_history"
        ) or _demo_history()
        peak_hours = forecaster.get_peak_hours(history_for_peaks)

        # --- Grace period reservations for dashboard ---
        grace_list = [
            {'slot_label': f"Slot {sid}", 'user_name': g['user_name'],
             'minutes_left': g['minutes_left'],
             'grace_expires': g['grace_expires'].strftime('%Y-%m-%d %H:%M')}
            for sid, g in grace_map.items()
        ]

        # Serialise datetimes
        for r in reservations + confirmed_reservations:
            for key in ('start_time', 'end_time', 'created_at', 'qr_verified_at'):
                if isinstance(r.get(key), datetime):
                    r[key] = r[key].strftime('%Y-%m-%d %H:%M')

        return jsonify({
            'success': True,
            'summary': {
                'total': total,
                'occupied': occupied_or_reserved,
                'available': available,
                'confirmed': len(confirmed_reservations),
                'occupancy_pct': round((occupied_or_reserved / total) * 100, 1) if total else 0
            },
            'reservations': reservations,
            'confirmed_reservations': confirmed_reservations,
            'overstays': overstays,
            'grace_reservations': grace_list,
            'peak_hours': peak_hours[:10],
            'history': history[:168],
            'slots': slots
        })
    except Exception as e:
        logger.error(f"/dashboard-data error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  GET /predict
# ---------------------------------------------------------------------------
@app.route('/predict', methods=['GET'])
def predict():
    """
    Return occupancy predictions for the next N hours.
    Query param: hours (default 6).
    """
    try:
        hours = int(request.args.get('hours', 6))
        hours = max(1, min(hours, 24))

        # Train / retrain model from history
        history = query_db(
            "SELECT hour_of_day, day_of_week, occupied_slots FROM occupancy_history"
        ) or _demo_history()
        forecaster.train(history)

        predictions = forecaster.predict_next_hours(hours_ahead=hours)

        return jsonify({
            'success': True,
            'total_slots': TOTAL_SLOTS,
            'predictions': predictions
        })
    except Exception as e:
        logger.error(f"/predict error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  POST /detect  (trigger a detection scan)
# ---------------------------------------------------------------------------
@app.route('/detect', methods=['POST'])
def run_detection():
    """
    Trigger a vehicle detection scan and update slot statuses.
    """
    try:
        slots = query_db("SELECT slot_id, slot_label, x1, y1, x2, y2 FROM parking_slots") or []
        if not slots:
            slots = _demo_slot_coords()

        source = int(VIDEO_SOURCE) if VIDEO_SOURCE.isdigit() else VIDEO_SOURCE
        slot_results, _ = detector.process_frame_from_source(source, slots)

        if not slot_results:
            # Use simulation as fallback
            import random
            for s in slots:
                slot_results[s['slot_id']] = {
                    'slot_label': s['slot_label'],
                    'is_occupied': random.choice([True, False]),
                    'confidence': round(random.uniform(0.6, 0.99), 2)
                }

        # Update database — but protect slots in grace period
        grace_map = _get_grace_reservations()
        for sid, result in slot_results.items():
            if sid in grace_map and not result['is_occupied']:
                # Only protect the slot if the car genuinely hasn't arrived yet
                # (i.e. the DB also still shows it as unoccupied).
                # If the car already parked (is_occupied was True) and has now left,
                # let detection clear it normally.
                slot_in_db = query_db(
                    "SELECT is_occupied FROM parking_slots WHERE slot_id = %s",
                    (sid,), fetchone=True
                )
                if slot_in_db and not slot_in_db['is_occupied']:
                    # Car never arrived — keep the slot protected during grace window
                    continue
            query_db(
                "UPDATE parking_slots SET is_occupied = %s WHERE slot_id = %s",
                (result['is_occupied'], sid), commit=True
            )

        # Cancel expired grace-period reservations
        _check_expired_grace()

        # Record occupancy snapshot
        occupied_count = sum(1 for r in slot_results.values() if r['is_occupied'])
        now = datetime.now()
        query_db(
            """INSERT INTO occupancy_history
               (timestamp, total_slots, occupied_slots, available_slots, hour_of_day, day_of_week)
               VALUES (%s, %s, %s, %s, %s, %s)""",
            (now, TOTAL_SLOTS, occupied_count, TOTAL_SLOTS - occupied_count,
             now.hour, now.weekday()),
            commit=True
        )

        return jsonify({
            'success': True,
            'message': 'Detection scan complete.',
            'results': {str(k): v for k, v in slot_results.items()}
        })
    except Exception as e:
        logger.error(f"/detect error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  POST /release/<slot_id>
# ---------------------------------------------------------------------------
@app.route('/release/<int:slot_id>', methods=['POST'])
def release_slot(slot_id):
    """Mark a slot as available and complete its active reservation."""
    try:
        query_db(
            "UPDATE parking_slots SET is_occupied = FALSE WHERE slot_id = %s",
            (slot_id,), commit=True
        )
        query_db(
            """UPDATE reservations SET status = 'completed'
               WHERE slot_id = %s AND status = 'active'""",
            (slot_id,), commit=True
        )
        return jsonify({'success': True, 'message': f'Slot {slot_id} released.'})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  POST /cancel-reservation/<reservation_id>
# ---------------------------------------------------------------------------
@app.route('/cancel-reservation/<int:reservation_id>', methods=['POST'])
def cancel_reservation(reservation_id):
    """Cancel an active/confirmed reservation, free the slot, and notify user by email."""
    try:
        # Get full reservation details (need user_email for cancellation notice)
        res = query_db(
            """SELECT r.slot_id, r.user_name, r.user_email, r.car_plate,
                      r.start_time, r.end_time, p.slot_label
               FROM reservations r
               JOIN parking_slots p ON r.slot_id = p.slot_id
               WHERE r.reservation_id = %s AND r.status IN ('active','confirmed')""",
            (reservation_id,), fetchone=True
        )
        if not res:
            return jsonify({'success': False, 'error': 'Reservation not found or already cancelled.'}), 404

        # Cancel the reservation
        query_db(
            "UPDATE reservations SET status = 'cancelled' WHERE reservation_id = %s",
            (reservation_id,), commit=True
        )

        # Free the slot
        query_db(
            "UPDATE parking_slots SET is_occupied = FALSE WHERE slot_id = %s",
            (res['slot_id'],), commit=True
        )

        # Send cancellation email in background thread
        user_email = res.get('user_email', '')
        if user_email and MAIL_USER and MAIL_PASSWORD:
            cancel_info = {
                'reservation_id': reservation_id,
                'user_name': res['user_name'],
                'car_plate': res.get('car_plate') or 'N/A',
                'user_email': user_email,
                'slot_label': res.get('slot_label') or f"Slot {res['slot_id']}",
                'start_time': res['start_time'] if isinstance(res['start_time'], datetime) else datetime.strptime(str(res['start_time']), '%Y-%m-%d %H:%M:%S'),
                'end_time': res['end_time'] if isinstance(res['end_time'], datetime) else datetime.strptime(str(res['end_time']), '%Y-%m-%d %H:%M:%S'),
            }
            t = threading.Thread(
                target=send_cancellation_email,
                args=(cancel_info,),
                daemon=True
            )
            t.start()

        return jsonify({'success': True, 'message': f'Reservation #{reservation_id} cancelled.'})
    except Exception as e:
        logger.error(f"/cancel-reservation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  GET /verify/<reservation_id>  (QR scan landing page)
# ---------------------------------------------------------------------------
@app.route('/verify/<int:reservation_id>', methods=['GET'])
def verify_page(reservation_id):
    """Render the QR verification page for admin to confirm a reservation."""
    return render_template('verify.html', reservation_id=reservation_id)


# ---------------------------------------------------------------------------
# API  –  POST /verify-reservation/<reservation_id>
# ---------------------------------------------------------------------------
@app.route('/verify-reservation/<int:reservation_id>', methods=['POST'])
def verify_reservation(reservation_id):
    """Admin scans QR → mark reservation as confirmed."""
    try:
        res = query_db(
            """SELECT r.*, p.slot_label
               FROM reservations r
               JOIN parking_slots p ON r.slot_id = p.slot_id
               WHERE r.reservation_id = %s AND r.status = 'active'""",
            (reservation_id,), fetchone=True
        )
        if not res:
            return jsonify({'success': False, 'error': 'Reservation not found or not in active state.'}), 404

        # Check if grace period has expired
        created = res.get('created_at') or res.get('start_time')
        if isinstance(created, str):
            created = datetime.strptime(created, '%Y-%m-%d %H:%M:%S')
        grace_deadline = created + timedelta(minutes=GRACE_PERIOD_MINUTES)
        now = datetime.now()
        if now > grace_deadline:
            return jsonify({'success': False, 'error': 'Grace period has expired. Reservation cannot be confirmed.'}), 410

        # Confirm the reservation
        query_db(
            "UPDATE reservations SET status = 'confirmed', qr_verified_at = %s WHERE reservation_id = %s",
            (now, reservation_id), commit=True
        )

        # Send confirmation email
        user_email = res.get('user_email', '')
        if user_email and MAIL_USER and MAIL_PASSWORD:
            confirm_info = {
                'reservation_id': reservation_id,
                'user_name': res['user_name'],
                'car_plate': res.get('car_plate') or 'N/A',
                'user_email': user_email,
                'slot_label': res.get('slot_label') or f"Slot {res['slot_id']}",
                'start_time': res['start_time'] if isinstance(res['start_time'], datetime) else datetime.strptime(str(res['start_time']), '%Y-%m-%d %H:%M:%S'),
                'end_time': res['end_time'] if isinstance(res['end_time'], datetime) else datetime.strptime(str(res['end_time']), '%Y-%m-%d %H:%M:%S'),
            }
            t = threading.Thread(
                target=send_qr_confirmed_email,
                args=(confirm_info,),
                daemon=True
            )
            t.start()

        return jsonify({
            'success': True,
            'message': f'Reservation #{reservation_id} confirmed! {res["user_name"]} is verified for slot {res.get("slot_label", res["slot_id"])}.'
        })
    except Exception as e:
        logger.error(f"/verify-reservation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  GET /extend/<reservation_id>  (extension page)
# ---------------------------------------------------------------------------
@app.route('/extend/<int:reservation_id>', methods=['GET'])
def extend_page(reservation_id):
    """Render the reservation extension page."""
    return render_template('extend.html', reservation_id=reservation_id)


# ---------------------------------------------------------------------------
# API  –  POST /extend-reservation/<reservation_id>
# ---------------------------------------------------------------------------
@app.route('/extend-reservation/<int:reservation_id>', methods=['POST'])
def extend_reservation(reservation_id):
    """
    Extend a confirmed reservation's end_time.
    Expects JSON: { new_end_time: 'YYYY-MM-DDTHH:MM' }
    """
    try:
        data = request.get_json(force=True)
        new_end_str = data.get('new_end_time', '').strip()
        if not new_end_str:
            return jsonify({'success': False, 'error': 'new_end_time is required.'}), 400

        new_end_time = datetime.strptime(new_end_str, '%Y-%m-%dT%H:%M')

        res = query_db(
            """SELECT r.*, p.slot_label
               FROM reservations r
               JOIN parking_slots p ON r.slot_id = p.slot_id
               WHERE r.reservation_id = %s AND r.status = 'confirmed'""",
            (reservation_id,), fetchone=True
        )
        if not res:
            return jsonify({'success': False, 'error': 'Reservation not found or not confirmed.'}), 404

        current_end = res['end_time'] if isinstance(res['end_time'], datetime) else datetime.strptime(str(res['end_time']), '%Y-%m-%d %H:%M:%S')
        if new_end_time <= current_end:
            return jsonify({'success': False, 'error': 'New end time must be after current end time.'}), 400

        # Check for conflicts with other reservations on same slot
        conflict = query_db(
            """SELECT reservation_id FROM reservations
               WHERE slot_id = %s AND status IN ('active','confirmed')
               AND reservation_id != %s
               AND start_time < %s AND end_time > %s""",
            (res['slot_id'], reservation_id, new_end_time, current_end),
            fetchone=True
        )
        if conflict:
            return jsonify({'success': False, 'error': 'Cannot extend — another reservation conflicts with the new time.'}), 409

        # Extend the reservation and reset end reminder flag
        query_db(
            "UPDATE reservations SET end_time = %s, end_reminder_sent = FALSE WHERE reservation_id = %s",
            (new_end_time, reservation_id), commit=True
        )

        slot_label = res.get('slot_label') or f"Slot {res['slot_id']}"

        # Send extension confirmation email
        user_email = res.get('user_email', '')
        if user_email and MAIL_USER and MAIL_PASSWORD:
            ext_info = {
                'reservation_id': reservation_id,
                'user_name': res['user_name'],
                'car_plate': res.get('car_plate') or 'N/A',
                'user_email': user_email,
                'slot_label': slot_label,
                'old_end_time': current_end,
                'new_end_time': new_end_time,
            }
            t = threading.Thread(target=send_extension_email, args=(ext_info,), daemon=True)
            t.start()

        return jsonify({
            'success': True,
            'message': f'Reservation #{reservation_id} extended to {new_end_time.strftime("%d %b %Y, %I:%M %p")} for slot {slot_label}.'
        })
    except ValueError:
        return jsonify({'success': False, 'error': 'Invalid date format. Use YYYY-MM-DDTHH:MM'}), 400
    except Exception as e:
        logger.error(f"/extend-reservation error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# API  –  GET /reservation-details/<reservation_id>  (for verify/extend pages)
# ---------------------------------------------------------------------------
@app.route('/reservation-details/<int:reservation_id>', methods=['GET'])
def reservation_details(reservation_id):
    """Return reservation details as JSON for the verify/extend pages."""
    try:
        res = query_db(
            """SELECT r.*, p.slot_label
               FROM reservations r
               JOIN parking_slots p ON r.slot_id = p.slot_id
               WHERE r.reservation_id = %s""",
            (reservation_id,), fetchone=True
        )
        if not res:
            return jsonify({'success': False, 'error': 'Reservation not found.'}), 404

        # Compute grace info
        created = res.get('created_at') or res.get('start_time')
        if isinstance(created, str):
            created = datetime.strptime(created, '%Y-%m-%d %H:%M:%S')
        grace_deadline = created + timedelta(minutes=GRACE_PERIOD_MINUTES)
        now = datetime.now()
        grace_expired = now > grace_deadline
        grace_minutes_left = max(0, int((grace_deadline - now).total_seconds() / 60)) if not grace_expired else 0

        for key in ('start_time', 'end_time', 'created_at', 'qr_verified_at'):
            if isinstance(res.get(key), datetime):
                res[key] = res[key].strftime('%Y-%m-%d %H:%M')

        res['grace_expired'] = grace_expired
        res['grace_minutes_left'] = grace_minutes_left

        return jsonify({'success': True, 'reservation': res})
    except Exception as e:
        logger.error(f"/reservation-details error: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500


# ---------------------------------------------------------------------------
# Helpers – grace period management
# ---------------------------------------------------------------------------
def _get_grace_reservations():
    """
    Return a dict of slot_id -> grace info for all active reservations
    still within the 15-minute arrival window.
    """
    grace_map = {}
    now = datetime.now()
    reservations = query_db(
        """SELECT reservation_id, slot_id, user_name, start_time, created_at
           FROM reservations
           WHERE status = 'active'"""
    ) or []
    for res in reservations:
        created = res.get('created_at') or res.get('start_time')
        if isinstance(created, str):
            created = datetime.strptime(created, '%Y-%m-%d %H:%M:%S')
        grace_deadline = created + timedelta(minutes=GRACE_PERIOD_MINUTES)
        if now < grace_deadline:
            minutes_left = max(0, int((grace_deadline - now).total_seconds() / 60))
            grace_map[res['slot_id']] = {
                'reservation_id': res['reservation_id'],
                'user_name': res['user_name'],
                'grace_expires': grace_deadline,
                'minutes_left': minutes_left,
            }
    return grace_map


def _check_expired_grace():
    """
    Auto-cancel reservations where the grace period expired
    and the car has NOT arrived (slot still not occupied).
    """
    now = datetime.now()
    cutoff = now - timedelta(minutes=GRACE_PERIOD_MINUTES)
    expired = query_db(
        """SELECT r.reservation_id, r.slot_id, p.is_occupied
           FROM reservations r
           JOIN parking_slots p ON r.slot_id = p.slot_id
           WHERE r.status = 'active'
           AND r.created_at <= %s
           AND p.is_occupied = FALSE""",
        (cutoff,)
    ) or []
    for res in expired:
        # Use 'cancelled' — 'expired' is not in the MySQL ENUM
        query_db(
            "UPDATE reservations SET status = 'cancelled' WHERE reservation_id = %s",
            (res['reservation_id'],), commit=True
        )
        logger.info(f"Grace expired: reservation #{res['reservation_id']} on slot {res['slot_id']} auto-cancelled.")


# ---------------------------------------------------------------------------
# Helpers – PDF token generation & email
# ---------------------------------------------------------------------------
def _build_reservation_pdf(info: dict) -> bytes:
    """
    Generate a styled parking token PDF with QR code in-memory and return raw bytes.
    `info` keys: reservation_id, user_name, car_plate, user_email,
                 slot_label, start_time (datetime), end_time (datetime)
    """
    buf = io.BytesIO()
    doc = SimpleDocTemplate(
        buf,
        pagesize=A5,
        leftMargin=12*mm, rightMargin=12*mm,
        topMargin=12*mm, bottomMargin=12*mm,
    )

    styles = getSampleStyleSheet()
    # Custom styles
    header_style = ParagraphStyle(
        'header', fontSize=20, leading=24,
        textColor=colors.HexColor('#1e3a5f'),
        alignment=TA_CENTER, fontName='Helvetica-Bold'
    )
    sub_style = ParagraphStyle(
        'sub', fontSize=10, leading=13,
        textColor=colors.HexColor('#475569'),
        alignment=TA_CENTER, fontName='Helvetica'
    )
    token_style = ParagraphStyle(
        'token', fontSize=13, leading=16,
        textColor=colors.HexColor('#0f172a'),
        alignment=TA_CENTER, fontName='Helvetica-Bold'
    )
    label_style = ParagraphStyle(
        'label', fontSize=9,
        textColor=colors.HexColor('#64748b'),
        fontName='Helvetica'
    )
    value_style = ParagraphStyle(
        'value', fontSize=11,
        textColor=colors.HexColor('#0f172a'),
        fontName='Helvetica-Bold'
    )

    duration_mins = int((info['end_time'] - info['start_time']).total_seconds() / 60)
    duration_str  = f"{duration_mins // 60}h {duration_mins % 60}m" if duration_mins >= 60 else f"{duration_mins}m"

    story = [
        Paragraph('[ P ] SmartPark', header_style),
        Paragraph('Parking Reservation Token', sub_style),
        Spacer(1, 4*mm),
        HRFlowable(width='100%', thickness=1, color=colors.HexColor('#1e3a5f')),
        Spacer(1, 4*mm),
        Paragraph(f"Token #{info['reservation_id']:06d}", token_style),
        Spacer(1, 6*mm),
    ]

    # Details table
    table_data = [
        [Paragraph('Name', label_style),        Paragraph(info['user_name'], value_style)],
        [Paragraph('Car Plate', label_style),   Paragraph(info['car_plate'] or 'N/A', value_style)],
        [Paragraph('Email', label_style),       Paragraph(info['user_email'] or 'N/A', value_style)],
        [Paragraph('Slot', label_style),        Paragraph(info['slot_label'], value_style)],
        [Paragraph('Date', label_style),        Paragraph(info['start_time'].strftime('%d %B %Y'), value_style)],
        [Paragraph('Start Time', label_style),  Paragraph(info['start_time'].strftime('%I:%M %p'), value_style)],
        [Paragraph('End Time', label_style),    Paragraph(info['end_time'].strftime('%I:%M %p'), value_style)],
        [Paragraph('Duration', label_style),    Paragraph(duration_str, value_style)],
    ]

    col_w = (A5[0] - 24*mm)
    tbl = Table(table_data, colWidths=[col_w * 0.35, col_w * 0.65])
    tbl.setStyle(TableStyle([
        ('BACKGROUND', (0, 0), (-1, 0), colors.HexColor('#e0f2fe')),
        ('ROWBACKGROUNDS', (0, 0), (-1, -1), [colors.HexColor('#f8fafc'), colors.white]),
        ('GRID',       (0, 0), (-1, -1), 0.4, colors.HexColor('#cbd5e1')),
        ('VALIGN',     (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 4),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 4),
        ('LEFTPADDING',   (0, 0), (-1, -1), 6),
    ]))

    story.append(tbl)
    story.append(Spacer(1, 5*mm))

    # --- QR Code ---
    verify_url = f"{APP_HOST}/verify/{info['reservation_id']}"
    qr_img = qrcode.make(verify_url, box_size=6, border=2)
    qr_buf = io.BytesIO()
    qr_img.save(qr_buf, format='PNG')
    qr_buf.seek(0)

    qr_label_style = ParagraphStyle(
        'qr_label', fontSize=8, alignment=TA_CENTER, leading=10,
        textColor=colors.HexColor('#475569'), fontName='Helvetica'
    )
    # Center QR code with label in a table
    qr_rl_image = RLImage(qr_buf, width=32*mm, height=32*mm)
    qr_table = Table(
        [[qr_rl_image],
         [Paragraph('Scan QR at gate for admin verification', qr_label_style)]],
        colWidths=[col_w]
    )
    qr_table.setStyle(TableStyle([
        ('ALIGN', (0, 0), (-1, -1), 'CENTER'),
        ('VALIGN', (0, 0), (-1, -1), 'MIDDLE'),
        ('TOPPADDING', (0, 0), (-1, -1), 2),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 2),
    ]))
    story.append(qr_table)
    story.append(Spacer(1, 3*mm))

    story.append(HRFlowable(width='100%', thickness=0.5, color=colors.HexColor('#cbd5e1')))
    story.append(Spacer(1, 3*mm))
    issued_str = datetime.now().strftime('%d %b %Y, %I:%M %p')
    story.append(Paragraph(
        'Issued: ' + issued_str + '  |  Status: ACTIVE',
        ParagraphStyle('footer', fontSize=8, alignment=TA_CENTER,
                       textColor=colors.HexColor('#94a3b8'), fontName='Helvetica')
    ))
    story.append(Spacer(1, 4*mm))
    warn_style = ParagraphStyle('warn', fontSize=9, alignment=TA_CENTER, leading=13,
                                textColor=colors.HexColor('#92400e'), fontName='Helvetica-Bold')
    warn_sub   = ParagraphStyle('warn_sub', fontSize=8, alignment=TA_CENTER, leading=11,
                                textColor=colors.HexColor('#92400e'), fontName='Helvetica')
    col_w2 = A5[0] - 24*mm
    warn_tbl = Table(
        [[Paragraph('! This token is valid for 15 minutes from the time of issue.', warn_style)],
         [Paragraph('Present this QR code to the admin at the gate for verification.', warn_sub)]],
        colWidths=[col_w2]
    )
    warn_tbl.setStyle(TableStyle([
        ('BACKGROUND',    (0, 0), (-1, -1), colors.HexColor('#fef3c7')),
        ('BOX',           (0, 0), (-1, -1), 1, colors.HexColor('#f59e0b')),
        ('TOPPADDING',    (0, 0), (-1, -1), 6),
        ('BOTTOMPADDING', (0, 0), (-1, -1), 6),
        ('LEFTPADDING',   (0, 0), (-1, -1), 8),
        ('RIGHTPADDING',  (0, 0), (-1, -1), 8),
        ('VALIGN',        (0, 0), (-1, -1), 'MIDDLE'),
    ]))
    story.append(warn_tbl)
    story.append(Spacer(1, 3*mm))
    story.append(Paragraph(
        'Present this token at the parking entry gate.',
        ParagraphStyle('note', fontSize=8, alignment=TA_CENTER,
                       textColor=colors.HexColor('#94a3b8'), fontName='Helvetica-Oblique')
    ))

    doc.build(story)
    return buf.getvalue()


def send_reservation_email(info: dict):
    """
    Build a PDF parking token and send it to the user's email via Gmail SMTP.
    Called in a background thread so the HTTP response is not delayed.
    """
    try:
        pdf_bytes = _build_reservation_pdf(info)

        msg = MIMEMultipart()
        msg['From']    = f'SmartPark <{MAIL_USER}>'
        msg['To']      = info['user_email']
        msg['Subject'] = f"[SmartPark] Parking Token #{info['reservation_id']:06d} - {info['slot_label']}"

        body = (
            f"Hi {info['user_name']},\n\n"
            f"Your parking slot has been successfully reserved. "
            f"Please find your parking token attached as a PDF.\n\n"
            f"  Slot       : {info['slot_label']}\n"
            f"  Car Plate  : {info['car_plate'] or 'N/A'}\n"
            f"  Start Time : {info['start_time'].strftime('%d %b %Y, %I:%M %p')}\n"
            f"  End Time   : {info['end_time'].strftime('%d %b %Y, %I:%M %p')}\n\n"
            f"Please present the attached PDF token at the parking gate.\n\n"
            f"-- SmartPark Team"
        )
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        pdf_attachment = MIMEApplication(pdf_bytes, _subtype='pdf')
        pdf_attachment.add_header(
            'Content-Disposition', 'attachment',
            filename=f"parking_token_{info['reservation_id']:06d}.pdf"
        )
        msg.attach(pdf_attachment)

        context = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.send_message(msg)

        logger.info(f"Reservation email sent to {info['user_email']} (token #{info['reservation_id']})")
    except Exception as e:
        logger.error(f"send_reservation_email failed: {e}")


def send_cancellation_email(info: dict):
    """
    Send a cancellation notice email to the user when admin cancels their reservation.
    Called in a background thread so the HTTP response is not delayed.
    """
    try:
        msg = MIMEMultipart()
        msg['From']    = f'SmartPark <{MAIL_USER}>'
        msg['To']      = info['user_email']
        msg['Subject'] = f"[SmartPark] Reservation #{info['reservation_id']:06d} Cancelled"

        body = (
            f"Hi {info['user_name']},\n\n"
            f"We regret to inform you that your parking reservation has been cancelled "
            f"by the administrator.\n\n"
            f"  Reservation # : {info['reservation_id']:06d}\n"
            f"  Slot          : {info['slot_label']}\n"
            f"  Car Plate     : {info['car_plate']}\n"
            f"  Start Time    : {info['start_time'].strftime('%d %b %Y, %I:%M %p')}\n"
            f"  End Time      : {info['end_time'].strftime('%d %b %Y, %I:%M %p')}\n\n"
            f"If you believe this was done in error, please contact the parking "
            f"administration office.\n\n"
            f"We apologize for any inconvenience.\n\n"
            f"-- SmartPark Team"
        )
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        context = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.send_message(msg)

        logger.info(f"Cancellation email sent to {info['user_email']} (reservation #{info['reservation_id']})")
    except Exception as e:
        logger.error(f"send_cancellation_email failed: {e}")


def send_grace_warning_email(info: dict):
    """
    Send a warning email when only 5 minutes remain in the grace period
    and the QR has not been verified yet.
    """
    try:
        msg = MIMEMultipart()
        msg['From']    = f'SmartPark <{MAIL_USER}>'
        msg['To']      = info['user_email']
        msg['Subject'] = f"[SmartPark] ⚠️ {GRACE_WARNING_MINUTES} min left to verify! Reservation #{info['reservation_id']:06d}"

        body = (
            f"Hi {info['user_name']},\n\n"
            f"Your parking reservation has NOT been verified yet!\n\n"
            f"You have only {GRACE_WARNING_MINUTES} minutes remaining to present your \n"
            f"QR code at the parking gate for admin verification.\n\n"
            f"  Reservation # : {info['reservation_id']:06d}\n"
            f"  Slot          : {info['slot_label']}\n"
            f"  Car Plate     : {info['car_plate']}\n\n"
            f"If your reservation is not verified within the grace period, \n"
            f"it will be automatically cancelled.\n\n"
            f"Please hurry to the parking gate!\n\n"
            f"-- SmartPark Team"
        )
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        context = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.send_message(msg)

        logger.info(f"Grace warning email sent to {info['user_email']} (reservation #{info['reservation_id']})")
    except Exception as e:
        logger.error(f"send_grace_warning_email failed: {e}")


def send_qr_confirmed_email(info: dict):
    """
    Send a confirmation email when admin scans the QR and verifies the reservation.
    """
    try:
        msg = MIMEMultipart()
        msg['From']    = f'SmartPark <{MAIL_USER}>'
        msg['To']      = info['user_email']
        msg['Subject'] = f"[SmartPark] ✅ Reservation #{info['reservation_id']:06d} Confirmed!"

        body = (
            f"Hi {info['user_name']},\n\n"
            f"Great news! Your parking reservation has been verified and confirmed \n"
            f"by the admin. Enjoy your parking spot!\n\n"
            f"  Reservation # : {info['reservation_id']:06d}\n"
            f"  Slot          : {info['slot_label']}\n"
            f"  Car Plate     : {info['car_plate']}\n"
            f"  Start Time    : {info['start_time'].strftime('%d %b %Y, %I:%M %p')}\n"
            f"  End Time      : {info['end_time'].strftime('%d %b %Y, %I:%M %p')}\n\n"
            f"You will receive a reminder {END_REMINDER_MINUTES} minutes before your \n"
            f"reservation ends, with an option to extend.\n\n"
            f"-- SmartPark Team"
        )
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        context = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.send_message(msg)

        logger.info(f"QR confirmed email sent to {info['user_email']} (reservation #{info['reservation_id']})")
    except Exception as e:
        logger.error(f"send_qr_confirmed_email failed: {e}")


def send_end_reminder_email(info: dict):
    """
    Send a reminder email when a CONFIRMED reservation is about to end
    (END_REMINDER_MINUTES before end_time). Includes a link to extend.
    """
    try:
        extend_url = f"{APP_HOST}/extend/{info['reservation_id']}"

        msg = MIMEMultipart()
        msg['From']    = f'SmartPark <{MAIL_USER}>'
        msg['To']      = info['user_email']
        msg['Subject'] = f"[SmartPark] ⏰ Reservation #{info['reservation_id']:06d} ending in {END_REMINDER_MINUTES} min!"

        body = (
            f"Hi {info['user_name']},\n\n"
            f"Your parking reservation is ending in approximately {END_REMINDER_MINUTES} minutes!\n\n"
            f"  Reservation # : {info['reservation_id']:06d}\n"
            f"  Slot          : {info['slot_label']}\n"
            f"  End Time      : {info['end_time'].strftime('%d %b %Y, %I:%M %p')}\n\n"
            f"Would you like to extend your reservation?\n"
            f"Click here to extend: {extend_url}\n\n"
            f"If you do not extend, please vacate the slot by your end time \n"
            f"to avoid overstay penalties.\n\n"
            f"-- SmartPark Team"
        )
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        context = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.send_message(msg)

        logger.info(f"End reminder email sent to {info['user_email']} (reservation #{info['reservation_id']})")
    except Exception as e:
        logger.error(f"send_end_reminder_email failed: {e}")


def send_extension_email(info: dict):
    """
    Send a confirmation email when a user successfully extends their reservation.
    """
    try:
        msg = MIMEMultipart()
        msg['From']    = f'SmartPark <{MAIL_USER}>'
        msg['To']      = info['user_email']
        msg['Subject'] = f"[SmartPark] ✅ Reservation #{info['reservation_id']:06d} Extended Successfully!"

        body = (
            f"Hi {info['user_name']},\n\n"
            f"Your parking reservation has been successfully extended!\n\n"
            f"  Reservation # : {info['reservation_id']:06d}\n"
            f"  Slot          : {info['slot_label']}\n"
            f"  Car Plate     : {info['car_plate']}\n"
            f"  Previous End  : {info['old_end_time'].strftime('%d %b %Y, %I:%M %p')}\n"
            f"  New End Time  : {info['new_end_time'].strftime('%d %b %Y, %I:%M %p')}\n\n"
            f"You will receive another reminder {END_REMINDER_MINUTES} minutes before \n"
            f"your new end time. Enjoy your extended parking!\n\n"
            f"-- SmartPark Team"
        )
        msg.attach(MIMEText(body, 'plain', 'utf-8'))

        context = ssl.create_default_context()
        with smtplib.SMTP('smtp.gmail.com', 587) as smtp:
            smtp.ehlo()
            smtp.starttls(context=context)
            smtp.login(MAIL_USER, MAIL_PASSWORD)
            smtp.send_message(msg)

        logger.info(f"Extension email sent to {info['user_email']} (reservation #{info['reservation_id']})")
    except Exception as e:
        logger.error(f"send_extension_email failed: {e}")


# ---------------------------------------------------------------------------
# Helpers – overstay logging
# ---------------------------------------------------------------------------
def _log_overstay(reservation):
    """Insert an overstay violation if not already logged."""
    existing = query_db(
        "SELECT violation_id FROM overstay_violations WHERE reservation_id = %s",
        (reservation['reservation_id'],), fetchone=True
    )
    if not existing:
        query_db(
            """INSERT INTO overstay_violations
               (reservation_id, slot_id, user_name, expected_end)
               VALUES (%s, %s, %s, %s)""",
            (reservation['reservation_id'], reservation['slot_id'],
             reservation['user_name'], reservation['end_time']),
            commit=True
        )


# ---------------------------------------------------------------------------
# Helpers – demo / fallback data (when DB is unavailable)
# ---------------------------------------------------------------------------
def _demo_slots():
    """Return demo slot data for offline development."""
    import random
    labels = ['A1','A2','A3','A4','B1','B2','B3','B4','C1','C2','C3','C4']
    coords = [
        (50,50,200,200),(220,50,370,200),(390,50,540,200),(560,50,710,200),
        (50,250,200,400),(220,250,370,400),(390,250,540,400),(560,250,710,400),
        (50,450,200,600),(220,450,370,600),(390,450,540,600),(560,450,710,600),
    ]
    return [
        {
            'slot_id': i + 1,
            'slot_label': labels[i],
            'x1': coords[i][0], 'y1': coords[i][1],
            'x2': coords[i][2], 'y2': coords[i][3],
            'is_occupied': random.choice([True, False, False]),
            'updated_at': datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        }
        for i in range(12)
    ]


def _demo_slot_coords():
    """Return just the coordinates for detection."""
    labels = ['A1','A2','A3','A4','B1','B2','B3','B4','C1','C2','C3','C4']
    coords = [
        (50,50,200,200),(220,50,370,200),(390,50,540,200),(560,50,710,200),
        (50,250,200,400),(220,250,370,400),(390,250,540,400),(560,250,710,400),
        (50,450,200,600),(220,450,370,600),(390,450,540,600),(560,450,710,600),
    ]
    return [{'slot_id': i+1, 'slot_label': labels[i],
             'x1': c[0], 'y1': c[1], 'x2': c[2], 'y2': c[3]}
            for i, c in enumerate(coords)]


def _demo_history():
    """Generate sample occupancy history for the last 7 days."""
    import random
    records = []
    now = datetime.now()
    for day_offset in range(7, 0, -1):
        for hour in range(24):
            ts = now - timedelta(days=day_offset, hours=(23 - hour))
            # Simulate realistic occupancy pattern
            base = {
                0: 1, 1: 1, 2: 0, 3: 0, 4: 0, 5: 1,
                6: 2, 7: 4, 8: 6, 9: 8, 10: 10, 11: 11,
                12: 10, 13: 11, 14: 10, 15: 9, 16: 7, 17: 6,
                18: 4, 19: 3, 20: 2, 21: 2, 22: 1, 23: 1
            }.get(hour, 5)
            occ = max(0, min(12, base + random.randint(-1, 1)))
            records.append({
                'record_id': len(records) + 1,
                'timestamp': ts.strftime('%Y-%m-%d %H:%M:%S'),
                'total_slots': 12,
                'occupied_slots': occ,
                'available_slots': 12 - occ,
                'hour_of_day': hour,
                'day_of_week': ts.weekday()
            })
    return records


# ---------------------------------------------------------------------------
# Background auto-detection thread
# ---------------------------------------------------------------------------
def _background_detection_loop():
    """Continuously run detection every DETECTION_INTERVAL seconds."""
    logger.info(f"Background detection started (every {DETECTION_INTERVAL}s)")
    time.sleep(5)  # wait for app to fully start
    while True:
        try:
            slots = query_db("SELECT slot_id, slot_label, x1, y1, x2, y2 FROM parking_slots") or []
            if not slots:
                time.sleep(DETECTION_INTERVAL)
                continue

            source = int(VIDEO_SOURCE) if VIDEO_SOURCE.isdigit() else VIDEO_SOURCE
            slot_results, _ = detector.process_frame_from_source(source, slots)

            if slot_results:
                for sid, result in slot_results.items():
                    query_db(
                        "UPDATE parking_slots SET is_occupied = %s WHERE slot_id = %s",
                        (result['is_occupied'], sid), commit=True
                    )
                occupied_count = sum(1 for r in slot_results.values() if r['is_occupied'])
                now = datetime.now()
                query_db(
                    """INSERT INTO occupancy_history
                       (timestamp, total_slots, occupied_slots, available_slots, hour_of_day, day_of_week)
                       VALUES (%s, %s, %s, %s, %s, %s)""",
                    (now, TOTAL_SLOTS, occupied_count, TOTAL_SLOTS - occupied_count,
                     now.hour, now.weekday()),
                    commit=True
                )
                logger.info(f"Auto-detection: {occupied_count}/{TOTAL_SLOTS} occupied")
        except Exception as e:
            logger.error(f"Background detection error: {e}")
        time.sleep(DETECTION_INTERVAL)


# ---------------------------------------------------------------------------
# Background alert thread (grace warnings + end-time reminders)
# ---------------------------------------------------------------------------
def _background_alert_loop():
    """
    Every 60 seconds, check for:
    1. Active reservations where grace period has ≤ GRACE_WARNING_MINUTES left
       and grace_warning_sent = FALSE → send warning email
    2. Confirmed reservations where end_time is within END_REMINDER_MINUTES
       and end_reminder_sent = FALSE → send end reminder email
    """
    logger.info("Background alert loop started (every 60s)")
    time.sleep(10)  # wait for app to fully start
    while True:
        try:
            now = datetime.now()

            # --- 1. Grace period warning (5 min left, QR not scanned) ---
            # Find active reservations where:
            #   created_at + (GRACE_PERIOD - GRACE_WARNING) <= now < created_at + GRACE_PERIOD
            #   AND grace_warning_sent = FALSE
            warning_cutoff_start = now - timedelta(minutes=GRACE_PERIOD_MINUTES - GRACE_WARNING_MINUTES)
            warning_cutoff_end = now - timedelta(minutes=GRACE_PERIOD_MINUTES)
            grace_warn_rows = query_db(
                """SELECT r.reservation_id, r.user_name, r.user_email, r.car_plate,
                          r.slot_id, r.start_time, r.end_time, p.slot_label
                   FROM reservations r
                   JOIN parking_slots p ON r.slot_id = p.slot_id
                   WHERE r.status = 'active'
                   AND r.grace_warning_sent = FALSE
                   AND r.user_email IS NOT NULL AND r.user_email != ''
                   AND r.created_at <= %s
                   AND r.created_at > %s""",
                (warning_cutoff_start, warning_cutoff_end)
            ) or []

            for row in grace_warn_rows:
                info = {
                    'reservation_id': row['reservation_id'],
                    'user_name': row['user_name'],
                    'user_email': row['user_email'],
                    'car_plate': row.get('car_plate') or 'N/A',
                    'slot_label': row.get('slot_label') or f"Slot {row['slot_id']}",
                }
                if MAIL_USER and MAIL_PASSWORD:
                    t = threading.Thread(target=send_grace_warning_email, args=(info,), daemon=True)
                    t.start()
                # Mark as sent
                query_db(
                    "UPDATE reservations SET grace_warning_sent = TRUE WHERE reservation_id = %s",
                    (row['reservation_id'],), commit=True
                )
                logger.info(f"Grace warning queued for reservation #{row['reservation_id']}")

            # --- 2. End-time reminder (10 min before end, only for CONFIRMED) ---
            reminder_cutoff = now + timedelta(minutes=END_REMINDER_MINUTES)
            end_remind_rows = query_db(
                """SELECT r.reservation_id, r.user_name, r.user_email, r.car_plate,
                          r.slot_id, r.start_time, r.end_time, p.slot_label
                   FROM reservations r
                   JOIN parking_slots p ON r.slot_id = p.slot_id
                   WHERE r.status = 'confirmed'
                   AND r.end_reminder_sent = FALSE
                   AND r.user_email IS NOT NULL AND r.user_email != ''
                   AND r.end_time <= %s
                   AND r.end_time > %s""",
                (reminder_cutoff, now)
            ) or []

            for row in end_remind_rows:
                end_t = row['end_time'] if isinstance(row['end_time'], datetime) else datetime.strptime(str(row['end_time']), '%Y-%m-%d %H:%M:%S')
                info = {
                    'reservation_id': row['reservation_id'],
                    'user_name': row['user_name'],
                    'user_email': row['user_email'],
                    'car_plate': row.get('car_plate') or 'N/A',
                    'slot_label': row.get('slot_label') or f"Slot {row['slot_id']}",
                    'end_time': end_t,
                }
                if MAIL_USER and MAIL_PASSWORD:
                    t = threading.Thread(target=send_end_reminder_email, args=(info,), daemon=True)
                    t.start()
                # Mark as sent
                query_db(
                    "UPDATE reservations SET end_reminder_sent = TRUE WHERE reservation_id = %s",
                    (row['reservation_id'],), commit=True
                )
                logger.info(f"End reminder queued for reservation #{row['reservation_id']}")

        except Exception as e:
            logger.error(f"Background alert loop error: {e}")
        time.sleep(60)


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == '__main__':
    # Start background detection thread (only once in debug reloader)
    if os.environ.get('WERKZEUG_RUN_MAIN') == 'true' or not app.debug:
        detection_thread = threading.Thread(target=_background_detection_loop, daemon=True)
        detection_thread.start()
        alert_thread = threading.Thread(target=_background_alert_loop, daemon=True)
        alert_thread.start()
    app.run(debug=True, host='0.0.0.0', port=5000)
