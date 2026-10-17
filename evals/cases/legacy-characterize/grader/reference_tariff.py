from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal, InvalidOperation

MAX_WEIGHT = 30
REMOTE_FEE = 450
MEMBER_CAP = 600
MINIMUM_FEE = 500


def _weight(value):
    try:
        w = Decimal(str(value))
    except (InvalidOperation, ValueError):
        raise ValueError("bad weight") from None

    if not w.is_finite():
        raise ValueError("bad weight")
    if not 0 < w <= MAX_WEIGHT:
        raise ValueError("bad weight")
    return w


def _zone(z):
    if not isinstance(z, str):
        raise ValueError("bad zone")
    z = z.strip().lower()
    if z not in ("local", "country", "remote"):
        raise ValueError("bad zone")
    return z


def _base(u, z):
    if u <= 2:
        b = 520
    elif u <= 6:
        b = 520 + (u - 2) * 135
    else:
        b = 1060 + (u - 6) * 95

    if z != "local":
        b += 180
    return b


def quote(weight, zone="local", member=False, express=False):
    w = _weight(weight)
    z = _zone(zone)
    if type(member) is not bool:
        raise ValueError("bad member")
    if type(express) is not bool:
        raise ValueError("bad express")

    u = int((w * 2).to_integral_value(rounding=ROUND_CEILING))
    b = _base(u, z)

    r = 0
    if z == "remote":
        r = REMOTE_FEE

    d = 0
    if member:
        d = min((b * 12) // 100, MEMBER_CAP)

    x = 0
    if express:
        x = int((Decimal(b) / 4).to_integral_value(rounding=ROUND_HALF_UP))

    total = max(MINIMUM_FEE, b + r + x - d)
    floor = total - (b + r + x - d)
    return {
        "zone": z,
        "grams": u * 500,
        "base": b,
        "remote": r,
        "express": x,
        "discount": d,
        "minimum": floor,
        "total": total,
    }
