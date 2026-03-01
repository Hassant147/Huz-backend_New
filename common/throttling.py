from rest_framework.throttling import AnonRateThrottle, UserRateThrottle


class OTPAnonRateThrottle(AnonRateThrottle):
    rate = "3/min"


class OTPUserRateThrottle(UserRateThrottle):
    rate = "10/min"
