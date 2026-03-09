def get_expected_traveller_count(booking):
    return max(
        int(getattr(booking, "adults", 0) or 0)
        + int(getattr(booking, "child", 0) or 0)
        + int(getattr(booking, "infants", 0) or 0),
        0,
    )
