import unittest
from datetime import datetime, timedelta, timezone

from app.services.location_risk import LocationRiskInput, assess_location_risk


def _base_input() -> LocationRiskInput:
    now = datetime(2026, 3, 27, 10, 0, tzinfo=timezone.utc)
    return LocationRiskInput(
        lat=10.776889,
        lng=106.700806,
        accuracy_m=10,
        timestamp_client=now,
        server_time=now,
        ip="1.1.1.1",
        user_agent="Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/123.0.0.0 Safari/537.36",
        accept_language="vi-VN,vi;q=0.9,en-US;q=0.8",
        ip_geo_lat=10.78,
        ip_geo_lng=106.69,
        ip_asn="VNPT",
        ip_proxy_or_vpn=False,
        risk_policy_version="v1",
        distance_to_geofence_m=50,
        radius_m=300,
        is_out_of_range=False,
        previous_action_time=now - timedelta(minutes=30),
        previous_action_lat=10.77,
        previous_action_lng=106.70,
    )


class LocationRiskScoringTestCase(unittest.TestCase):
    def test_allow_when_signals_are_normal(self) -> None:
        result = assess_location_risk(_base_input())
        self.assertEqual(result.score, 0)
        self.assertEqual(result.level, "LOW")
        self.assertEqual(result.decision, "ALLOW")
        self.assertEqual(result.flags, [])

    def test_allow_with_exception_with_multiple_medium_signals(self) -> None:
        base = _base_input()
        payload = LocationRiskInput(
            **{
                **base.__dict__,
                "accuracy_m": 150,  # +20
                "distance_to_geofence_m": 700,  # outside 400m => +25
            }
        )
        result = assess_location_risk(payload)
        self.assertEqual(result.score, 45)
        self.assertEqual(result.level, "MEDIUM")
        self.assertEqual(result.decision, "ALLOW_WITH_EXCEPTION")
        self.assertIn("BAD_ACCURACY", result.flags)
        self.assertIn("OUT_OF_GEOFENCE_300M", result.flags)

    def test_block_when_score_reaches_high_threshold(self) -> None:
        base = _base_input()
        payload = LocationRiskInput(
            **{
                **base.__dict__,
                "accuracy_m": 180,  # +20
                "distance_to_geofence_m": 1700,  # outside >1000 => +40
                "ip_proxy_or_vpn": True,  # +15
            }
        )
        result = assess_location_risk(payload)
        self.assertEqual(result.score, 75)
        self.assertEqual(result.level, "HIGH")
        self.assertEqual(result.decision, "BLOCK")

    def test_impossible_speed_flag(self) -> None:
        base = _base_input()
        now = base.server_time
        payload = LocationRiskInput(
            **{
                **base.__dict__,
                "previous_action_time": now - timedelta(minutes=1),
                "previous_action_lat": 11.0,
                "previous_action_lng": 107.0,
            }
        )
        result = assess_location_risk(payload)
        self.assertIn("IMPOSSIBLE_SPEED", result.flags)
        self.assertGreaterEqual(result.score, 35)

    def test_score_is_clamped_to_100(self) -> None:
        base = _base_input()
        payload = LocationRiskInput(
            **{
                **base.__dict__,
                "accuracy_m": 500,  # +20
                "distance_to_geofence_m": 5000,  # +40
                "ip_geo_lat": 21.0,  # +20
                "ip_geo_lng": 105.0,
                "ip_proxy_or_vpn": True,  # +15
                "user_agent": "",  # +5 spoof hint
                "previous_action_time": base.server_time - timedelta(seconds=10),
                "previous_action_lat": 12.0,
                "previous_action_lng": 108.0,  # +35 impossible speed
            }
        )
        result = assess_location_risk(payload)
        self.assertEqual(result.score, 100)
        self.assertEqual(result.decision, "BLOCK")


if __name__ == "__main__":
    unittest.main()
