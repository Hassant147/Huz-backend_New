from datetime import timedelta

from django.db import migrations, models
from django.utils import timezone


BOOKING_STATUS_HOLD = "HOLD"
BOOKING_STATUS_TRAVELER_DETAILS_PENDING = "TRAVELER_DETAILS_PENDING"
BOOKING_STATUS_AWAITING_FINAL_PAYMENT = "AWAITING_FINAL_PAYMENT"
BOOKING_STATUS_READY_FOR_OPERATOR = "READY_FOR_OPERATOR"
BOOKING_STATUS_IN_FULFILLMENT = "IN_FULFILLMENT"
BOOKING_STATUS_READY_FOR_TRAVEL = "READY_FOR_TRAVEL"
BOOKING_STATUS_COMPLETED = "COMPLETED"
BOOKING_STATUS_CANCELLED = "CANCELLED"
BOOKING_STATUS_EXPIRED = "EXPIRED"

PAYMENT_STATUS_UNDER_REVIEW = "UNDER_REVIEW"
PAYMENT_STATUS_APPROVED = "APPROVED"
PAYMENT_STATUS_REJECTED = "REJECTED"
PAYMENT_STATUS_NOT_SUBMITTED = "NOT_SUBMITTED"

ISSUE_STATUS_NONE = "NONE"
ISSUE_STATUS_OPERATOR_OBJECTION = "OPERATOR_OBJECTION"
ISSUE_STATUS_REPORTED = "REPORTED"


def _to_local_date(value):
    if value is None:
        return None
    if timezone.is_aware(value):
        value = timezone.localtime(value)
    return value.date()


def _normalize_payment_status(value):
    normalized = str(value or "").strip().lower()
    if normalized in {"pending", "under_review"}:
        return PAYMENT_STATUS_UNDER_REVIEW
    if normalized == "approved":
        return PAYMENT_STATUS_APPROVED
    if normalized == "rejected":
        return PAYMENT_STATUS_REJECTED
    return PAYMENT_STATUS_NOT_SUBMITTED


def _payment_stage_status(payments, stage):
    normalized_stage = stage.lower()
    for payment in payments:
        payment_stage = str(getattr(payment, "transaction_type", "") or "").strip().lower()
        if payment_stage == normalized_stage:
            return _normalize_payment_status(getattr(payment, "payment_status", ""))
    return PAYMENT_STATUS_NOT_SUBMITTED


def _latest_rejected_time(payments):
    for payment in payments:
        if _normalize_payment_status(getattr(payment, "payment_status", "")) == PAYMENT_STATUS_REJECTED:
            return getattr(payment, "transaction_time", None)
    return None


def _passports_complete(passports, booking):
    expected = max(int(getattr(booking, "adults", 0) or 0) + int(getattr(booking, "child", 0) or 0) + int(getattr(booking, "infants", 0) or 0), 0)
    if len(passports) < expected:
        return False
    return all(
        passport.user_passport
        and passport.user_photo
        and passport.first_name
        and passport.last_name
        and passport.date_of_birth
        and passport.passport_number
        and passport.passport_country
        and passport.expiry_date
        for passport in passports[:expected]
    )


def _documents_complete(document_status):
    if document_status is None:
        return False
    return all(
        bool(getattr(document_status, field_name, False))
        for field_name in (
            "is_visa_completed",
            "is_airline_completed",
            "is_airline_detail_completed",
            "is_hotel_completed",
            "is_transport_completed",
        )
    )


def migrate_booking_workflow(apps, schema_editor):
    Booking = apps.get_model("booking", "Booking")
    Payment = apps.get_model("booking", "Payment")
    PassportValidity = apps.get_model("booking", "PassportValidity")
    DocumentsStatus = apps.get_model("booking", "DocumentsStatus")

    now = timezone.now()

    for payment in Payment.objects.all().iterator():
        normalized_status = _normalize_payment_status(payment.payment_status)
        if payment.payment_status != normalized_status:
            payment.payment_status = normalized_status
            payment.save(update_fields=["payment_status"])

    bookings = Booking.objects.all().order_by("order_time")
    for booking in bookings.iterator():
        payments = list(Payment.objects.filter(booking_token=booking).order_by("-transaction_time"))
        passports = list(PassportValidity.objects.filter(passport_for_booking_number=booking).order_by("passport_id"))
        document_status = DocumentsStatus.objects.filter(status_for_booking=booking).first()

        minimum_status = _payment_stage_status(payments, "minimum")
        full_status = _payment_stage_status(payments, "full")
        passports_complete = _passports_complete(passports, booking)
        latest_rejected_time = _latest_rejected_time(payments)
        booking_status = str(booking.booking_status or "").strip().lower()
        issue_status = ISSUE_STATUS_NONE
        hold_expires_at = None
        correction_expires_at = None

        if booking_status == "objection":
            issue_status = ISSUE_STATUS_OPERATOR_OBJECTION
        elif booking_status == "report":
            issue_status = ISSUE_STATUS_REPORTED

        if booking_status == "cancel":
            next_status = BOOKING_STATUS_CANCELLED
        elif booking_status == "active":
            next_status = BOOKING_STATUS_IN_FULFILLMENT
        elif booking_status == "closed":
            next_status = BOOKING_STATUS_COMPLETED
        elif booking_status in {"completed", "report"}:
            if booking.end_date and _to_local_date(now) > _to_local_date(booking.end_date):
                next_status = BOOKING_STATUS_COMPLETED
            else:
                next_status = BOOKING_STATUS_READY_FOR_TRAVEL
        elif minimum_status == PAYMENT_STATUS_APPROVED:
            if not passports_complete:
                next_status = BOOKING_STATUS_TRAVELER_DETAILS_PENDING
            elif full_status == PAYMENT_STATUS_APPROVED:
                next_status = BOOKING_STATUS_READY_FOR_OPERATOR
            else:
                next_status = BOOKING_STATUS_AWAITING_FINAL_PAYMENT
        else:
            next_status = BOOKING_STATUS_HOLD

        if booking_status == "initialize" and not payments:
            hold_expires_at = (booking.order_time or now) + timedelta(minutes=15)
            if now > hold_expires_at:
                next_status = BOOKING_STATUS_EXPIRED
        elif latest_rejected_time is not None:
            correction_expires_at = latest_rejected_time + timedelta(hours=2)
            if now > correction_expires_at:
                next_status = BOOKING_STATUS_EXPIRED
        elif next_status == BOOKING_STATUS_HOLD and minimum_status == PAYMENT_STATUS_NOT_SUBMITTED:
            hold_expires_at = (booking.order_time or now) + timedelta(minutes=15)
            if now > hold_expires_at:
                next_status = BOOKING_STATUS_EXPIRED

        if next_status == BOOKING_STATUS_EXPIRED:
            hold_expires_at = None
            correction_expires_at = None

        booking.booking_status = next_status
        booking.issue_status = issue_status
        booking.hold_expires_at = hold_expires_at
        booking.payment_correction_expires_at = correction_expires_at
        booking.status_changed_at = now
        booking.is_payment_received = minimum_status == PAYMENT_STATUS_APPROVED or full_status == PAYMENT_STATUS_APPROVED
        booking.save(
            update_fields=[
                "booking_status",
                "issue_status",
                "hold_expires_at",
                "payment_correction_expires_at",
                "status_changed_at",
                "is_payment_received",
            ]
        )


class Migration(migrations.Migration):
    dependencies = [
        ("booking", "0002_add_hotspot_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="booking",
            name="issue_status",
            field=models.CharField(
                choices=[
                    (ISSUE_STATUS_NONE, ISSUE_STATUS_NONE.lower()),
                    (ISSUE_STATUS_OPERATOR_OBJECTION, ISSUE_STATUS_OPERATOR_OBJECTION.lower()),
                    (ISSUE_STATUS_REPORTED, ISSUE_STATUS_REPORTED.lower()),
                ],
                default=ISSUE_STATUS_NONE,
                max_length=32,
            ),
        ),
        migrations.AddField(
            model_name="booking",
            name="hold_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="booking",
            name="payment_correction_expires_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="booking",
            name="status_changed_at",
            field=models.DateTimeField(default=timezone.now),
            preserve_default=False,
        ),
        migrations.AlterField(
            model_name="booking",
            name="booking_status",
            field=models.CharField(
                choices=[
                    (BOOKING_STATUS_HOLD, BOOKING_STATUS_HOLD.lower()),
                    (BOOKING_STATUS_TRAVELER_DETAILS_PENDING, BOOKING_STATUS_TRAVELER_DETAILS_PENDING.lower()),
                    (BOOKING_STATUS_AWAITING_FINAL_PAYMENT, BOOKING_STATUS_AWAITING_FINAL_PAYMENT.lower()),
                    (BOOKING_STATUS_READY_FOR_OPERATOR, BOOKING_STATUS_READY_FOR_OPERATOR.lower()),
                    (BOOKING_STATUS_IN_FULFILLMENT, BOOKING_STATUS_IN_FULFILLMENT.lower()),
                    (BOOKING_STATUS_READY_FOR_TRAVEL, BOOKING_STATUS_READY_FOR_TRAVEL.lower()),
                    (BOOKING_STATUS_COMPLETED, BOOKING_STATUS_COMPLETED.lower()),
                    (BOOKING_STATUS_CANCELLED, BOOKING_STATUS_CANCELLED.lower()),
                    (BOOKING_STATUS_EXPIRED, BOOKING_STATUS_EXPIRED.lower()),
                ],
                max_length=40,
            ),
        ),
        migrations.AlterField(
            model_name="payment",
            name="payment_status",
            field=models.CharField(
                blank=True,
                choices=[
                    (PAYMENT_STATUS_UNDER_REVIEW, PAYMENT_STATUS_UNDER_REVIEW.lower()),
                    (PAYMENT_STATUS_APPROVED, PAYMENT_STATUS_APPROVED.lower()),
                    (PAYMENT_STATUS_REJECTED, PAYMENT_STATUS_REJECTED.lower()),
                ],
                max_length=50,
                null=True,
            ),
        ),
        migrations.RunPython(migrate_booking_workflow, migrations.RunPython.noop),
    ]
