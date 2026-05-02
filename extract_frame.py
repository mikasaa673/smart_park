import cv2
import sys

cap = cv2.VideoCapture('carPark.mp4')
ret, frame = cap.read()
cap.release()

if ret:
    h, w = frame.shape[:2]
    print(f"Video resolution: {w}x{h}")
    cv2.imwrite('carPark_frame.jpg', frame)
    print("Saved frame to carPark_frame.jpg — open it to see the parking layout")
else:
    print("ERROR: Could not read carPark.mp4 — check the file exists and is a valid video")
