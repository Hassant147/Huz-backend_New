from django.core.management.base import BaseCommand
from django.db import transaction

from booking.models import Booking
from booking.statuses import BOOKING_STATUS_CAPACITY_SET, BOOKING_STATUS_READY_FOR_TRAVEL
from booking.workflow import sync_booking_state


class Command(BaseCommand):
    help = "Expire booking holds/payment corrections and finalize ready-for-travel bookings after trip end."

    def handle(self, *args, **options):
        queryset = Booking.objects.filter(
            booking_status__in=list(BOOKING_STATUS_CAPACITY_SET | {BOOKING_STATUS_READY_FOR_TRAVEL})
        ).order_by("order_time")

        processed = 0
        changed = 0

        for booking in queryset.iterator():
            processed += 1
            previous_status = booking.booking_status
            previous_hold = booking.hold_expires_at
            previous_correction = booking.payment_correction_expires_at
            with transaction.atomic():
                sync_booking_state(booking, save=True)
            if (
                booking.booking_status != previous_status
                or booking.hold_expires_at != previous_hold
                or booking.payment_correction_expires_at != previous_correction
            ):
                changed += 1

        self.stdout.write(
            self.style.SUCCESS(
                f"Processed {processed} bookings and updated {changed} booking lifecycle records."
            )
        )
