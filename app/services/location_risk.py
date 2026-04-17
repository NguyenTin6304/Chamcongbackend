from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from app.services.geo import haversine_m

LocationRiskDecision = Literal["ALLOW", "ALLOW_WITH_EXCEPTION", "BLOCK"]
LocationRiskLevel = Literal["LOW", "MEDIUM", "HIGH"]


@dataclass(frozen=True)
class LocationRiskInput:
    lat: float
    lng: float
    accuracy_m: float | None
    timestamp_client: datetime | None
    server_time: datetime
    ip: str | None
    user_agent: str | None
    accept_language: str | None
    ip_geo_lat: float | None
    ip_geo_lng: float | None
    ip_asn: str | None
    ip_proxy_or_vpn: bool | None
    risk_policy_version: str
    distance_to_geofence_m: float
    radius_m: int
    is_out_of_range: bool
    previous_action_time: datetime | None
    previous_action_lat: float | None
    previous_action_lng: float | None
    recent_exact_coord_reuse_count: int


@dataclass(frozen=True)
class LocationRiskAssessment:
    score: int
    level: LocationRiskLevel
    decision: LocationRiskDecision
    flags: list[str]
    user_message: str
    policy_version: str


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _has_browser_spoof_hint(user_agent: str | None, accept_language: str | None) -> bool:
    ua = (user_agent or "").strip().lower()
    lang = (accept_language or "").strip().lower()
    if not ua:
        return True

    suspicious_tokens = (
        "headless",
        "phantomjs",
        "selenium",
        "playwright",
        "puppeteer",
        "curl/",
        "python-requests",
        "postmanruntime",
    )
    if any(token in ua for token in suspicious_tokens):
        return True

    if "mozilla/" in ua and not lang:
        return True
    return False


def _is_mobile_web_user_agent(user_agent: str | None) -> bool:
    ua = (user_agent or "").strip().lower()
    if not ua:
        return False
    mobile_tokens = ("mobile", "android", "iphone", "ipad")
    return any(token in ua for token in mobile_tokens)


def _is_datacenter_proxy_vpn_asn(ip_asn: str | None) -> bool:
    asn = (ip_asn or "").lower()
    if not asn:
        return False
    datacenter_keywords = (
        "amazon",
        "aws",
        "google cloud",
        "microsoft",
        "azure",
        "digitalocean",
        "linode",
        "ovh",
        "vultr",
        "oracle cloud",
        "choopa",
        "cloudflare",
        "hosting",
        "datacenter",
        "vpn",
        "proxy",
    )
    return any(keyword in asn for keyword in datacenter_keywords)


def _to_level(score: int) -> LocationRiskLevel:
    if score >= 70:
        return "HIGH"
    if score >= 40:
        return "MEDIUM"
    return "LOW"


def _to_decision(score: int) -> LocationRiskDecision:
    if score >= 70:
        return "BLOCK"
    if score >= 40:
        return "ALLOW_WITH_EXCEPTION"
    return "ALLOW"


def _build_user_message(level: LocationRiskLevel, flags: list[str]) -> str:
    if level == "LOW":
        return "Vị trí hợp lệ bạn có thể tiếp tục chấm công."
    if level == "MEDIUM":
        return (
            "Hệ thống vẫn ghi nhận chấm công nhưng phát hiện tín hiệu rủi ro vị trí. "
            "Vui lòng tắt VPN/fake GPS và bật định vị chính xác."
        )
    flag_preview = ", ".join(flags[:3]) if flags else "HIGH_RISK"
    return (
        "Hệ thống tạm thời từ chối chấm công do rủi ro vị trí cao. "
        f"Dấu hiệu: {flag_preview}. Vui lòng xác minh lại thiết bị/mạng."
    )


def assess_location_risk(payload: LocationRiskInput) -> LocationRiskAssessment:
    score = 0
    flags: list[str] = []
    is_mobile_web = _is_mobile_web_user_agent(payload.user_agent)
    has_network_context = (
        payload.ip_geo_lat is not None and payload.ip_geo_lng is not None
    ) or bool((payload.ip_asn or "").strip())

    accuracy = payload.accuracy_m
    if accuracy is not None and accuracy > 100:
        score += 20
        flags.append("BAD_ACCURACY")
    if is_mobile_web and not has_network_context:
        # Reduced from 20→10: Vercel UAT does not forward IP-geo headers, so this
        # fires for ALL mobile users on that deployment — not a reliable fraud signal.
        score += 10
        flags.append("MOBILE_WEB_MISSING_NETWORK_CONTEXT")

    outside_distance_m = max(0.0, float(payload.distance_to_geofence_m) - max(0, int(payload.radius_m)))
    if outside_distance_m > 1000:
        score += 40
        flags.append("OUT_OF_GEOFENCE_1000M")
    elif outside_distance_m > 300:
        score += 25
        flags.append("OUT_OF_GEOFENCE_300M")

    previous_time = payload.previous_action_time
    previous_lat = payload.previous_action_lat
    previous_lng = payload.previous_action_lng
    if previous_time is not None and previous_lat is not None and previous_lng is not None:
        delta_seconds = (_as_utc(payload.server_time) - _as_utc(previous_time)).total_seconds()
        if delta_seconds > 0:
            movement_distance = haversine_m(previous_lat, previous_lng, payload.lat, payload.lng)
            speed_kmh = (movement_distance / delta_seconds) * 3.6
            if speed_kmh > 180:
                score += 35
                flags.append("IMPOSSIBLE_SPEED")

    if payload.recent_exact_coord_reuse_count >= 3:
        score += 20
        flags.append("EXACT_COORD_REPEAT_PATTERN")

    if payload.ip_geo_lat is not None and payload.ip_geo_lng is not None:
        ip_gps_distance_m = haversine_m(payload.ip_geo_lat, payload.ip_geo_lng, payload.lat, payload.lng)
        if ip_gps_distance_m > 150000:
            score += 20
            flags.append("IP_GEO_MISMATCH")

    if payload.ip_proxy_or_vpn is True or _is_datacenter_proxy_vpn_asn(payload.ip_asn):
        score += 15
        flags.append("DATACENTER_PROXY_VPN_ASN")

    if _has_browser_spoof_hint(payload.user_agent, payload.accept_language):
        score += 5
        flags.append("BROWSER_SPOOF_HINT")

    score = max(0, min(100, score))
    level = _to_level(score)
    decision = _to_decision(score)

    return LocationRiskAssessment(
        score=score,
        level=level,
        decision=decision,
        flags=flags,
        user_message=_build_user_message(level, flags),
        policy_version=payload.risk_policy_version,
    )
