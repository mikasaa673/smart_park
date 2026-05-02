"""
vehicle_detection.py
--------------------
Parking slot occupancy detection using adaptive thresholding + pixel counting.
Best suited for top-down / overhead camera views.
"""

import cv2
import numpy as np
import logging

logger = logging.getLogger(__name__)


class VehicleDetector:
    """
    Detects vehicle occupancy in parking slots using adaptive thresholding.
    A slot is considered occupied when the non-zero pixel count in its region
    exceeds OCCUPANCY_THRESHOLD.
    """

    OCCUPANCY_THRESHOLD = 800  # tune this if detection is too sensitive/lenient

    def __init__(self, **_):
        logger.info("Detector initialised in 'threshold' mode.")

    # ── Pre-processing ────────────────────────────────────────────────────
    @staticmethod
    def preprocess_frame(frame):
        """Convert a BGR frame to a binary mask highlighting objects/vehicles."""
        gray    = cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY)
        blur    = cv2.GaussianBlur(gray, (3, 3), 1)
        thresh  = cv2.adaptiveThreshold(
            blur, 255,
            cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
            cv2.THRESH_BINARY_INV, 25, 16
        )
        median  = cv2.medianBlur(thresh, 5)
        dilated = cv2.dilate(median, np.ones((3, 3), np.uint8), iterations=1)
        return dilated

    # ── Core detection ────────────────────────────────────────────────────
    def check_slots_threshold(self, frame, slots):
        """
        Check each slot's occupancy by counting non-zero pixels in the
        preprocessed binary image.

        Args:
            frame: Original BGR frame.
            slots: List of dicts with slot_id, slot_label, x1, y1, x2, y2.

        Returns:
            Dict mapping slot_id -> { slot_label, is_occupied, confidence, pixel_count }
        """
        processed = self.preprocess_frame(frame)
        results = {}

        for slot in slots:
            x1, y1, x2, y2 = slot['x1'], slot['y1'], slot['x2'], slot['y2']
            crop        = processed[y1:y2, x1:x2]
            pixel_count = cv2.countNonZero(crop)
            occupied    = pixel_count >= self.OCCUPANCY_THRESHOLD
            slot_area   = max((x2 - x1) * (y2 - y1), 1)

            results[slot['slot_id']] = {
                'slot_label':  slot['slot_label'],
                'is_occupied': occupied,
                'confidence':  round(min(pixel_count / slot_area, 1.0), 2),
                'pixel_count': pixel_count,
            }

        occ = sum(1 for r in results.values() if r['is_occupied'])
        logger.info(f"Detected {occ}/{len(slots)} occupied (threshold mode).")
        return results

    # ── Public API ────────────────────────────────────────────────────────
    def process_frame(self, frame, slots):
        """Analyse a single frame and return slot occupancy results."""
        return self.check_slots_threshold(frame, slots)

    def process_frame_from_source(self, source, slots):
        """
        Read a single frame from a video file / camera and analyse it.

        Returns:
            Tuple of (slot_results_dict, annotated_frame_or_None)
        """
        try:
            cap = cv2.VideoCapture(source)
            ret, frame = cap.read()
            cap.release()

            if not ret or frame is None:
                logger.error("Could not read frame from source.")
                return {}, None

            slot_results = self.process_frame(frame, slots)
            return slot_results, self._annotate_frame(frame, slots, slot_results)

        except Exception as e:
            logger.error(f"Frame processing error: {e}")
            return {}, None

    # ── Annotation helper ─────────────────────────────────────────────────
    def _annotate_frame(self, frame, slots, slot_results):
        """Draw colour-coded slot rectangles on the frame."""
        annotated = frame.copy()
        for slot in slots:
            sid    = slot['slot_id']
            is_occ = slot_results.get(sid, {}).get('is_occupied', False)
            colour = (0, 0, 255) if is_occ else (0, 255, 0)
            cv2.rectangle(annotated, (slot['x1'], slot['y1']),
                          (slot['x2'], slot['y2']), colour, 2)
            px_count = slot_results.get(sid, {}).get('pixel_count', '')
            text = f"{slot['slot_label']} {px_count}" if px_count != '' else slot['slot_label']
            cv2.putText(annotated, text, (slot['x1'] + 2, slot['y2'] - 4),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.35, colour, 1)
        return annotated
