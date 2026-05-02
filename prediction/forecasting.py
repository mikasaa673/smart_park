"""
forecasting.py
--------------
Predictive analytics module for parking occupancy.
Uses historical data and Linear Regression to predict future availability.
"""

import numpy as np
import logging
from datetime import datetime, timedelta
from sklearn.linear_model import LinearRegression
from sklearn.preprocessing import PolynomialFeatures

logger = logging.getLogger(__name__)


class ParkingForecaster:
    """
    Predicts future parking occupancy using historical data
    and scikit-learn Linear Regression.
    """

    def __init__(self, total_slots=12):
        """
        Args:
            total_slots: Total number of parking slots in the facility.
        """
        self.total_slots = total_slots
        self.model = None
        self.poly = PolynomialFeatures(degree=2, include_bias=False)

    def train(self, history_records):
        """
        Train the prediction model on historical occupancy data.

        Args:
            history_records: List of dicts with keys:
                - hour_of_day (0-23)
                - day_of_week (0-6, Monday=0)
                - occupied_slots (int)
        """
        if not history_records or len(history_records) < 5:
            logger.warning("Not enough historical data to train model. Need at least 5 records.")
            self.model = None
            return

        try:
            X_raw = np.array([
                [r['hour_of_day'], r['day_of_week']]
                for r in history_records
            ])
            y = np.array([r['occupied_slots'] for r in history_records])

            X_poly = self.poly.fit_transform(X_raw)

            self.model = LinearRegression()
            self.model.fit(X_poly, y)

            score = self.model.score(X_poly, y)
            logger.info(f"Forecasting model trained. R² score: {score:.3f}")

        except Exception as e:
            logger.error(f"Model training error: {e}")
            self.model = None

    def predict_next_hours(self, hours_ahead=6):
        """
        Predict occupancy for the next N hours starting from now.

        Args:
            hours_ahead: Number of hours to forecast.

        Returns:
            List of dicts: [{ 'hour', 'day_of_week', 'predicted_occupied',
                              'predicted_available', 'occupancy_pct' }, ...]
        """
        now = datetime.now()
        predictions = []

        for i in range(1, hours_ahead + 1):
            future = now + timedelta(hours=i)
            hour = future.hour
            dow = future.weekday()

            if self.model is not None:
                X_input = self.poly.transform(np.array([[hour, dow]]))
                pred_occupied = float(self.model.predict(X_input)[0])
                # Clamp prediction to valid range
                pred_occupied = max(0, min(self.total_slots, round(pred_occupied, 1)))
            else:
                # Fallback: use a simple heuristic curve
                pred_occupied = self._heuristic_prediction(hour, dow)

            pred_available = round(self.total_slots - pred_occupied, 1)
            occupancy_pct = round((pred_occupied / self.total_slots) * 100, 1)

            predictions.append({
                'hour': f"{hour:02d}:00",
                'datetime': future.strftime('%Y-%m-%d %H:%M'),
                'day_of_week': dow,
                'predicted_occupied': pred_occupied,
                'predicted_available': pred_available,
                'occupancy_pct': occupancy_pct
            })

        return predictions

    def _heuristic_prediction(self, hour, day_of_week):
        """
        Simple heuristic when no trained model is available.
        Models a typical office-area parking pattern.
        """
        # Base occupancy curve (peaks at 10-14h office hours)
        hourly_pattern = {
            0: 0.10, 1: 0.05, 2: 0.05, 3: 0.05, 4: 0.05, 5: 0.10,
            6: 0.20, 7: 0.35, 8: 0.55, 9: 0.75, 10: 0.85, 11: 0.90,
            12: 0.88, 13: 0.90, 14: 0.85, 15: 0.75, 16: 0.65, 17: 0.50,
            18: 0.35, 19: 0.25, 20: 0.20, 21: 0.15, 22: 0.12, 23: 0.10
        }

        base = hourly_pattern.get(hour, 0.5)

        # Apply weekend discount (Sat=5, Sun=6)
        if day_of_week >= 5:
            base *= 0.4

        occupied = round(base * self.total_slots, 1)
        return max(0, min(self.total_slots, occupied))

    def get_peak_hours(self, history_records):
        """
        Analyse historical data to identify peak occupancy hours.

        Args:
            history_records: List of dicts with hour_of_day and occupied_slots.

        Returns:
            List of dicts sorted by average occupancy descending.
        """
        if not history_records:
            return self._default_peak_hours()

        # Group by hour
        hourly_data = {}
        for r in history_records:
            h = r['hour_of_day']
            hourly_data.setdefault(h, []).append(r['occupied_slots'])

        peak_data = []
        for hour, values in sorted(hourly_data.items()):
            avg = round(np.mean(values), 1)
            peak_data.append({
                'hour': f"{hour:02d}:00",
                'avg_occupied': avg,
                'avg_available': round(self.total_slots - avg, 1),
                'occupancy_pct': round((avg / self.total_slots) * 100, 1)
            })

        # Sort by occupancy descending
        peak_data.sort(key=lambda x: x['avg_occupied'], reverse=True)
        return peak_data

    def _default_peak_hours(self):
        """Return default peak hours when no history is available."""
        default = []
        for hour in range(24):
            occ = self._heuristic_prediction(hour, 2)  # assume Wednesday
            default.append({
                'hour': f"{hour:02d}:00",
                'avg_occupied': occ,
                'avg_available': round(self.total_slots - occ, 1),
                'occupancy_pct': round((occ / self.total_slots) * 100, 1)
            })
        default.sort(key=lambda x: x['avg_occupied'], reverse=True)
        return default
