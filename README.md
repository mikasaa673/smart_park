#  Smart Parking Availability System

A real-time AI-powered parking management system that uses **computer vision** to detect vehicle occupancy, manages slot reservations with **QR-based verification**, sends **email notifications**, and provides **predictive analytics** for future availability.

Built with **Flask**, **OpenCV**, **scikit-learn**, **MySQL**, and **ReportLab**.

---

##  Features

| Feature | Description |
|---|---|
| **Real-time Detection** | Adaptive thresholding + pixel counting on CCTV/video feed to detect occupied vs. free slots |
| **Live Video Stream** | MJPEG stream with color-coded slot overlays (🟢 free / 🔴 occupied) |
| **Slot Reservation** | Book slots with name, car plate, email, and time window — with double-booking prevention |
| **QR Verification** | PDF parking token with QR code emailed on booking; admin scans to confirm arrival |
| **Grace Period** | 15-min arrival window with automatic warning emails and auto-cancellation on expiry |
| **Reservation Extension** | Extend confirmed reservations via a dedicated page |
| **Overstay Detection** | Flags vehicles exceeding their booked time; logs violations |
| **Occupancy Forecasting** | Polynomial regression on historical data to predict future availability |
| **Admin Dashboard** | Live stats, reservation management, overstay alerts, peak-hour analysis |
| **Email Notifications** | Confirmation, cancellation, QR-verified, grace-warning, and end-time reminders via Gmail SMTP |

---

##  Tech Stack

| Layer | Technology |
|---|---|
| **Backend** | Python 3 · Flask · Jinja2 |
| **Database** | MySQL (`mysql-connector-python`) |
| **Computer Vision** | OpenCV — adaptive thresholding, Gaussian blur, pixel counting |
| **ML / Prediction** | scikit-learn — Linear Regression with Polynomial Features |
| **PDF Generation** | ReportLab |
| **QR Codes** | `qrcode[pil]` |
| **Email** | `smtplib` + SSL (Gmail SMTP) |
| **Frontend** | HTML · CSS · JavaScript |
| **Data** | NumPy · Pandas |

---

##  Project Structure

```
smart_parking/
├── app.py                          # Main Flask app — routes, APIs, email, PDF
├── requirements.txt                # Python dependencies
├── .gitignore                      # Git ignore rules
├── .env.example                    # Environment variable template
│
├── detection/
│   ├── __init__.py
│   └── vehicle_detection.py        # Adaptive threshold occupancy detector
│
├── prediction/
│   ├── __init__.py
│   └── forecasting.py              # Polynomial regression forecaster
│
├── database/
│   ├── models.sql                  # Full DB schema & seed data (68 slots)
│   └── mapped_slots.sql            # Slot coordinate inserts
│
├── templates/
│   ├── index.html                  # Main parking view with live feed
│   ├── reserve.html                # Slot reservation form
│   ├── dashboard.html              # Admin dashboard
│   ├── verify.html                 # QR verification page
│   └── extend.html                 # Reservation extension page
│
└── static/
    ├── css/style.css               # Styling
    ├── js/
    │   ├── main.js                 # Parking page logic
    │   └── dashboard.js            # Dashboard logic & charts
    └── flowchart.html              # System workflow visualization
```

---

##  Setup Guide

### Prerequisites

- **Python 3.9+**
- **MySQL 8.0+** (optional — app falls back to demo data)
- **pip** package manager

### 1. Clone the Repository

```bash
git clone https://github.com/<your-username>/smart-parking.git
cd smart-parking
```

### 2. Create a Virtual Environment

```bash
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install Dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure Environment Variables

Copy the example env file and fill in your values:

```bash
cp .env.example .env
```

Or set them directly:

```bash
# Windows PowerShell
$env:DB_HOST = "localhost"
$env:DB_USER = "root"
$env:DB_PASSWORD = "your_password"
$env:DB_NAME = "smart_parking"
$env:MAIL_USER = "your_email@gmail.com"
$env:MAIL_PASSWORD = "your_app_password"

# Linux / macOS
export DB_HOST=localhost
export DB_USER=root
export DB_PASSWORD=your_password
export DB_NAME=smart_parking
export MAIL_USER=your_email@gmail.com
export MAIL_PASSWORD=your_app_password
```

### 5. Set Up the Database (Optional)

```bash
mysql -u root -p < database/models.sql
```

> **Note:** If MySQL is not available, the app automatically uses demo/simulated data.

### 6. Add a Video Source (Optional)

Place a parking lot video file (e.g., `carPark.mp4`) in the project root, or set:

```bash
# Use a webcam
$env:VIDEO_SOURCE = "0"

# Use a video file
$env:VIDEO_SOURCE = "your_video.mp4"
```

### 7. Run the Application

```bash
python app.py
```

The server starts at **http://localhost:5000**.

---

## 🌐 Pages

| Page | URL |
|---|---|
| Parking View | http://localhost:5000/ |
| Reserve a Slot | http://localhost:5000/reserve-page |
| Admin Dashboard | http://localhost:5000/dashboard |

---

##  API Endpoints

| Method | Endpoint | Description |
|---|---|---|
| GET | `/slots` | Get all slot statuses |
| POST | `/reserve` | Reserve a slot (JSON body) |
| GET | `/dashboard-data` | Aggregated dashboard data |
| GET | `/predict?hours=6` | Predict occupancy for next N hours |
| POST | `/detect` | Trigger a vehicle detection scan |
| POST | `/release/<id>` | Release a slot and complete reservation |
| POST | `/cancel-reservation/<id>` | Cancel a reservation |
| GET | `/verify/<id>` | QR verification page |
| POST | `/verify-reservation/<id>` | Confirm reservation via QR |
| GET | `/extend/<id>` | Reservation extension page |
| POST | `/extend-reservation/<id>` | Extend a confirmed reservation |
| GET | `/reservation-pdf/<id>` | Download parking token PDF |

### Example — Reserve a Slot

```bash
curl -X POST http://localhost:5000/reserve \
  -H "Content-Type: application/json" \
  -d '{
    "user_name": "Alice",
    "car_plate": "KA-01-AB-1234",
    "user_email": "alice@example.com",
    "slot_id": 3,
    "start_time": "2026-05-02T10:00",
    "end_time": "2026-05-02T12:00"
  }'
```

---

## 📝 Environment Variables

| Variable | Default | Description |
|---|---|---|
| `DB_HOST` | `localhost` | MySQL host |
| `DB_USER` | `root` | MySQL username |
| `DB_PASSWORD` | `root` | MySQL password |
| `DB_NAME` | `smart_parking` | Database name |
| `VIDEO_SOURCE` | `carPark.mp4` | Video file path or camera index |
| `DETECTION_INTERVAL` | `30` | Seconds between auto-scans |
| `MAIL_USER` | _(empty)_ | Gmail address for notifications |
| `MAIL_PASSWORD` | _(empty)_ | Gmail App Password |
| `APP_HOST` | `http://localhost:5000` | Base URL for QR code links |

---

## 📌 Notes

- The system works in **demo mode** without MySQL or a camera.
- Video files (`.mp4`, `.mkv`) are excluded from git via `.gitignore` due to size.
- For email to work, you need a **Gmail App Password** (enable 2FA on your Google account first).
- For academic projects, the simulation mode provides realistic behaviour out of the box.
