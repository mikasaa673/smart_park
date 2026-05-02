"""
slot_mapper.py
--------------
Interactive tool to map parking slots on a video frame.

USAGE:
    python slot_mapper.py

CONTROLS:
    - Click 2 points to draw a rectangle (top-left, then bottom-right)
    - Press 'u' to undo the last slot
    - Press 'r' to reset all slots
    - Press 's' to save and generate SQL
    - Press 'q' or ESC to quit without saving
"""

import cv2
import json
import os
import sys

# ─── Configuration ─────────────────────────────────────────────────────────
VIDEO_FILE = 'carPark.mp4'
OUTPUT_SQL = 'database/mapped_slots.sql'
OUTPUT_JSON = 'mapped_slots.json'

# ─── State ─────────────────────────────────────────────────────────────────
slots = []           # List of (x1, y1, x2, y2)
click_points = []    # Temp: partial click for current rectangle
drawing = False

# ─── Mouse callback ────────────────────────────────────────────────────────
def mouse_handler(event, x, y, flags, param):
    global click_points, drawing
    frame = param['frame']

    if event == cv2.EVENT_LBUTTONDOWN:
        click_points.append((x, y))

        if len(click_points) == 1:
            # First click — just mark the point
            drawing = True

        elif len(click_points) == 2:
            # Second click — save the rectangle
            x1 = min(click_points[0][0], click_points[1][0])
            y1 = min(click_points[0][1], click_points[1][1])
            x2 = max(click_points[0][0], click_points[1][0])
            y2 = max(click_points[0][1], click_points[1][1])
            slots.append((x1, y1, x2, y2))
            click_points = []
            drawing = False
            print(f"  Slot {len(slots):>2d}: ({x1}, {y1}) → ({x2}, {y2})")

    elif event == cv2.EVENT_MOUSEMOVE and drawing:
        # Live preview — redraw frame with the partial rectangle
        param['preview_point'] = (x, y)


def draw_overlay(frame, preview_point=None):
    """Draw all recorded slots + any in-progress rectangle."""
    overlay = frame.copy()

    # Draw saved slots
    for i, (x1, y1, x2, y2) in enumerate(slots):
        cv2.rectangle(overlay, (x1, y1), (x2, y2), (0, 255, 0), 2)
        label = f"S{i+1}"
        (tw, th), _ = cv2.getTextSize(label, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        cx, cy = (x1 + x2) // 2 - tw // 2, (y1 + y2) // 2 + th // 2
        cv2.putText(overlay, label, (cx, cy), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 1)

    # Draw in-progress rectangle
    if len(click_points) == 1 and preview_point:
        cv2.rectangle(overlay, click_points[0], preview_point, (255, 255, 0), 1)
        cv2.circle(overlay, click_points[0], 4, (0, 255, 255), -1)

    # HUD
    h, w = overlay.shape[:2]
    hud_lines = [
        f"Slots mapped: {len(slots)}",
        "Click 2 corners per slot  |  U=Undo  R=Reset  S=Save  Q=Quit"
    ]
    for i, line in enumerate(hud_lines):
        cv2.putText(overlay, line, (10, h - 10 - (len(hud_lines) - 1 - i) * 22),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.55, (255, 255, 255), 1, cv2.LINE_AA)

    return overlay


def save_results():
    """Save slots as SQL and JSON."""
    # JSON
    data = []
    for i, (x1, y1, x2, y2) in enumerate(slots):
        data.append({
            'slot_id': i + 1,
            'slot_label': f'S{i+1}',
            'x1': x1, 'y1': y1, 'x2': x2, 'y2': y2
        })
    with open(OUTPUT_JSON, 'w') as f:
        json.dump(data, f, indent=2)
    print(f"\n✓ Saved {len(slots)} slots to {OUTPUT_JSON}")

    # SQL
    os.makedirs(os.path.dirname(OUTPUT_SQL), exist_ok=True)
    with open(OUTPUT_SQL, 'w') as f:
        f.write("-- Auto-generated slot coordinates from slot_mapper.py\n")
        f.write("-- Run this AFTER creating the parking_slots table\n\n")
        f.write("DELETE FROM parking_slots;\n\n")
        for i, (x1, y1, x2, y2) in enumerate(slots):
            sid = i + 1
            label = f'S{sid}'
            f.write(
                f"INSERT INTO parking_slots (slot_id, slot_label, x1, y1, x2, y2, is_occupied) "
                f"VALUES ({sid}, '{label}', {x1}, {y1}, {x2}, {y2}, FALSE);\n"
            )
        f.write(f"\n-- Total: {len(slots)} slots\n")
    print(f"✓ Saved SQL to {OUTPUT_SQL}")
    print(f"\n  To apply:  mysql -u root -p smart_parking < {OUTPUT_SQL}")


def main():
    # Read first frame
    cap = cv2.VideoCapture(VIDEO_FILE)
    ret, frame = cap.read()
    cap.release()

    if not ret:
        print(f"ERROR: Cannot read {VIDEO_FILE}")
        print(f"  Make sure the file exists in: {os.getcwd()}")
        sys.exit(1)

    h, w = frame.shape[:2]
    print(f"\n=== Slot Mapper ===")
    print(f"Video: {VIDEO_FILE}  ({w}x{h})")
    print(f"Click the TOP-LEFT corner, then BOTTOM-RIGHT corner of each slot.")
    print(f"Keys: U=Undo  R=Reset  S=Save+Quit  Q=Quit\n")

    # Window
    win_name = 'Slot Mapper — Click to draw slots'
    cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)

    # Scale window to fit screen (max 1280 wide)
    scale = min(1280 / w, 720 / h, 1.0)
    win_w, win_h = int(w * scale), int(h * scale)
    cv2.resizeWindow(win_name, win_w, win_h)

    params = {'frame': frame, 'preview_point': None}
    cv2.setMouseCallback(win_name, mouse_handler, params)

    while True:
        display = draw_overlay(frame, params.get('preview_point'))
        cv2.imshow(win_name, display)

        key = cv2.waitKey(30) & 0xFF

        if key == ord('q') or key == 27:  # Q or ESC
            print("Quit without saving.")
            break

        elif key == ord('u'):  # Undo
            if slots:
                removed = slots.pop()
                print(f"  Undid slot {len(slots)+1}: {removed}")
            else:
                print("  Nothing to undo.")

        elif key == ord('r'):  # Reset
            slots.clear()
            click_points.clear()
            print("  All slots cleared.")

        elif key == ord('s'):  # Save
            if slots:
                save_results()
            else:
                print("  No slots to save!")
            break

    cv2.destroyAllWindows()


if __name__ == '__main__':
    main()
