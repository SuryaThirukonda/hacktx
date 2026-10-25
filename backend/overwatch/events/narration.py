"""Callout and summary templates. Text is built only from measured, validated fields; unknowns are said as unknown."""

from __future__ import annotations

from ..schemas import RelativeKinematics

WORDS = {"responder_safety": ("Subject", "subject"), "search_rescue": ("Person", "person")}


def noun(profile: str, cap: bool = True) -> str:
    a, b = WORDS.get(profile, WORDS["responder_safety"])
    return a if cap else b


def motion_label(speed: float | None, closing: float | None) -> str:
    if speed is None:
        return "moving (speed unknown)"
    if speed < 0.35:
        base = "stationary"
    elif speed < 2.0:
        base = "walking"
    elif speed < 3.6:
        base = "jogging"
    else:
        base = "running"
    if base != "stationary" and closing is not None:
        if closing > 0.6 * speed:
            return f"{base} toward responder"
        if closing < -0.6 * speed:
            return f"{base} away"
        return f"{base}, crossing"
    return base


def where(k: RelativeKinematics) -> str:
    if k.clock is not None and k.range_m is not None:
        return f"{k.clock} o'clock, {k.range_m:.0f} meters"
    if k.range_m is not None:
        return f"{k.range_m:.0f} meters (bearing unknown)"
    return "position unknown"


def callout(profile: str, k: RelativeKinematics, kind: str) -> tuple[str, str]:
    """(display text, spoken text)."""
    n = noun(profile)
    loc = where(k)
    if kind == "CLOSING_FAST":
        tail = f", closing {k.closing_mps:.0f} meters per second" if k.closing_mps else ", closing fast"
        return f"{n} {loc}, closing fast ({k.closing_mps:.1f} m/s, TTC {k.ttc_s:.1f} s)" if k.ttc_s else f"{n} {loc}{tail}", f"{n} {loc}{tail}"
    if kind == "APPROACH":
        return f"{n} {loc}, approaching at {k.closing_mps:.1f} m/s", f"{n} {loc}, approaching"
    if kind == "POSITION_CALLOUT":
        moving = motion_label(k.subject_speed_mps, k.closing_mps)
        spoken = f"{n} {loc}"
        if k.closing_mps is not None and k.closing_mps > 0.6:
            spoken += f", closing {k.closing_mps:.0f} meters per second"
        return f"{n} {loc}, {moving}", spoken
    return f"{n} {loc}", f"{n} {loc}"


def summary(profile: str, k: RelativeKinematics, subject_state: str, drone_note: str, calib_note: str) -> str:
    n = noun(profile)
    if subject_state == "UNLOCKED":
        return f"No {noun(profile, False)} selected. {drone_note}. Calibration: {calib_note}."
    if not k.valid:
        return f"{n} {subject_state.lower()}: {k.reason}. {drone_note}. Calibration: {calib_note}."
    return (f"{n} {motion_label(k.subject_speed_mps, k.closing_mps)}, {where(k)} from responder. "
            f"{drone_note}. Calibration: {calib_note}.")
