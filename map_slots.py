"""
Map parking slots onto the video frame and save an annotated image.
This defines all visible slots in the parking_feed.mkv video.
"""
import cv2
import numpy as np

# Read frame
cap = cv2.VideoCapture('parking_feed.mkv')
ret, frame = cap.read()
cap.release()

if not ret:
    print("Failed to read video")
    exit(1)

# Define all visible parking slots (estimated from the 1920x1080 frame)
# Format: (label, x1, y1, x2, y2)
# The lot has roughly:
#   - Left block: 2 rows facing each other (A-row near camera, B-row further)
#   - Right block: 2 rows facing each other (C-row near camera, D-row further)
#   - Far-left block near camera edge (E-row)
#   - Far-right block (F-row)

slots = [
    # === ROW A: Left block, bottom row (closest to camera, left side) ===
    ("A1",  15,  760, 120, 920),
    ("A2",  125, 740, 230, 900),
    ("A3",  235, 720, 340, 880),
    ("A4",  345, 700, 450, 860),
    ("A5",  455, 680, 555, 840),
    ("A6",  560, 665, 660, 820),

    # === ROW B: Left block, top row (further from camera, left side) ===
    ("B1",  15,  560, 110, 700),
    ("B2",  115, 545, 210, 685),
    ("B3",  215, 530, 310, 665),
    ("B4",  315, 515, 410, 650),
    ("B5",  415, 500, 510, 635),
    ("B6",  515, 485, 610, 620),

    # === ROW C: Right block, bottom row (closer to camera, right side) ===
    ("C1",  720, 640, 830, 790),
    ("C2",  835, 625, 945, 775),
    ("C3",  950, 610, 1060, 760),
    ("C4",  1065, 595, 1175, 745),
    ("C5",  1180, 580, 1290, 730),
    ("C6",  1295, 565, 1400, 715),

    # === ROW D: Right block, top row (further from camera, right side) ===
    ("D1",  720, 460, 820, 590),
    ("D2",  825, 450, 925, 580),
    ("D3",  930, 440, 1030, 570),
    ("D4",  1035, 430, 1130, 555),
    ("D5",  1135, 420, 1230, 545),
    ("D6",  1235, 410, 1330, 535),

    # === ROW E: Far-right, near camera (bottom-right area) ===
    ("E1",  1350, 550, 1460, 700),
    ("E2",  1465, 540, 1570, 690),
    ("E3",  1575, 530, 1680, 680),
    ("E4",  1685, 520, 1790, 670),

    # === ROW F: Far-right, further from camera ===
    ("F1",  1400, 400, 1500, 520),
    ("F2",  1505, 390, 1600, 510),
    ("F3",  1605, 380, 1700, 500),
    ("F4",  1705, 370, 1800, 490),

    # === ROW G: Bottom-most row very close to camera (left-bottom) ===
    ("G1",  30,  910, 150, 1070),
    ("G2",  160, 890, 280, 1050),
    ("G3",  290, 870, 410, 1030),
    ("G4",  420, 855, 540, 1010),
    ("G5",  550, 840, 670, 995),
    ("G6",  680, 825, 800, 980),
    ("G7",  810, 810, 930, 965),
    ("G8",  940, 795, 1060, 950),
    ("G9",  1070, 780, 1190, 935),
    ("G10", 1200, 768, 1320, 920),
]

# Draw slots on frame
annotated = frame.copy()
for label, x1, y1, x2, y2 in slots:
    cv2.rectangle(annotated, (x1, y1), (x2, y2), (0, 255, 0), 2)
    cv2.putText(annotated, label, (x1+5, y1+20),
                cv2.FONT_HERSHEY_SIMPLEX, 0.45, (0, 255, 255), 1)

cv2.imwrite('static/slots_mapped.jpg', annotated)
print(f"Mapped {len(slots)} slots. Saved to static/slots_mapped.jpg")

# Generate SQL
print("\n-- SQL to insert slots:")
print("DELETE FROM parking_slots;")
print("ALTER TABLE parking_slots AUTO_INCREMENT = 1;")
print("INSERT INTO parking_slots (slot_label, x1, y1, x2, y2, is_occupied) VALUES")
lines = []
for label, x1, y1, x2, y2 in slots:
    lines.append(f"  ('{label}', {x1}, {y1}, {x2}, {y2}, FALSE)")
print(",\n".join(lines) + ";")
