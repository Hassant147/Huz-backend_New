from django.db.models import Case, CharField, F, IntegerField, OuterRef, Q, Subquery, Value, When
from django.db.models.functions import Coalesce
from django.utils import timezone

from .models import Payment
from .statuses import (
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_CANCELLED,
    BOOKING_STATUS_CAPACITY_SET,
    BOOKING_STATUS_COMPLETED,
    BOOKING_STATUS_EXPIRED,
    BOOKING_STATUS_HOLD,
    BOOKING_STATUS_IN_FULFILLMENT,
    BOOKING_STATUS_READY_FOR_OPERATOR,
    BOOKING_STATUS_READY_FOR_TRAVEL,
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    ISSUE_STATUS_OPERATOR_OBJECTION,
    ISSUE_STATUS_REPORTED,
    PAYMENT_STATUS_NOT_SUBMITTED,
    PAYMENT_STATUS_REJECTED,
    PAYMENT_STATUS_UNDER_REVIEW,
    WORKFLOW_BUCKET_COMPLETED,
    WORKFLOW_BUCKET_FULFILLMENT,
    WORKFLOW_BUCKET_HISTORY,
    WORKFLOW_BUCKET_ISSUES,
    WORKFLOW_BUCKET_REPORTED,
    WORKFLOW_BUCKET_READY,
    WORKFLOW_BUCKET_READY_FOR_TRAVEL,
    WORKFLOW_BUCKET_VIEW_ONLY,
)

USER_BOOKING_STATUS_BUCKET_ALL = "all"
USER_BOOKING_STATUS_BUCKET_ACTION_REQUIRED = "action_required"
USER_BOOKING_STATUS_BUCKET_UNDER_REVIEW = "under_review"
USER_BOOKING_STATUS_BUCKET_IN_PROGRESS = "in_progress"
USER_BOOKING_STATUS_BUCKET_COMPLETED = "completed"
USER_BOOKING_STATUS_BUCKET_CANCELLED_EXPIRED = "cancelled_expired"

USER_BOOKING_STATUS_BUCKETS = {
    USER_BOOKING_STATUS_BUCKET_ALL,
    USER_BOOKING_STATUS_BUCKET_ACTION_REQUIRED,
    USER_BOOKING_STATUS_BUCKET_UNDER_REVIEW,
    USER_BOOKING_STATUS_BUCKET_IN_PROGRESS,
    USER_BOOKING_STATUS_BUCKET_COMPLETED,
    USER_BOOKING_STATUS_BUCKET_CANCELLED_EXPIRED,
}

ISSUE_STATUSES = (ISSUE_STATUS_OPERATOR_OBJECTION, ISSUE_STATUS_REPORTED)
PARTNER_VISIBLE_BOOKING_STATUSES = (
    BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
    BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
    BOOKING_STATUS_READY_FOR_OPERATOR,
    BOOKING_STATUS_IN_FULFILLMENT,
    BOOKING_STATUS_READY_FOR_TRAVEL,
    BOOKING_STATUS_COMPLETED,
    BOOKING_STATUS_CANCELLED,
    BOOKING_STATUS_EXPIRED,
)


def _latest_payment_status_subquery(stage):
    return Subquery(
        Payment.objects.filter(
            booking_token=OuterRef("pk"),
            transaction_type__iexact=stage,
        )
        .order_by("-transaction_time")
        .values("payment_status")[:1],
        output_field=CharField(),
    )


def normalize_user_booking_status_bucket(value):
    normalized = str(value or "").strip().lower()
    if normalized in USER_BOOKING_STATUS_BUCKETS:
        return normalized
    return USER_BOOKING_STATUS_BUCKET_ALL


def annotate_booking_payment_statuses(queryset):
    return queryset.annotate(
        annotated_minimum_payment_status=Coalesce(
            _latest_payment_status_subquery("Minimum"),
            Value(PAYMENT_STATUS_NOT_SUBMITTED),
        ),
        annotated_full_payment_status=Coalesce(
            _latest_payment_status_subquery("Full"),
            Value(PAYMENT_STATUS_NOT_SUBMITTED),
        ),
    )


def annotate_effective_booking_status(queryset, *, now=None, today=None):
    now = now or timezone.now()
    today = today or timezone.localdate()
    queryset = annotate_booking_payment_statuses(queryset)

    return queryset.annotate(
        effective_booking_status=Case(
            When(
                Q(booking_status=BOOKING_STATUS_HOLD)
                & Q(hold_expires_at__isnull=False)
                & Q(hold_expires_at__lt=now)
                & Q(annotated_minimum_payment_status=PAYMENT_STATUS_NOT_SUBMITTED)
                & Q(annotated_full_payment_status=PAYMENT_STATUS_NOT_SUBMITTED),
                then=Value(BOOKING_STATUS_EXPIRED),
            ),
            When(
                Q(payment_correction_expires_at__isnull=False)
                & Q(payment_correction_expires_at__lt=now)
                & (
                    Q(annotated_minimum_payment_status=PAYMENT_STATUS_REJECTED)
                    | Q(annotated_full_payment_status=PAYMENT_STATUS_REJECTED)
                ),
                then=Value(BOOKING_STATUS_EXPIRED),
            ),
            When(
                Q(booking_status=BOOKING_STATUS_READY_FOR_TRAVEL)
                & ~Q(issue_status__in=ISSUE_STATUSES)
                & Q(end_date__date__lt=today),
                then=Value(BOOKING_STATUS_COMPLETED),
            ),
            default=F("booking_status"),
            output_field=CharField(),
        )
    )


def build_user_booking_under_review_q():
    return Q(
        effective_booking_status=BOOKING_STATUS_HOLD,
        annotated_minimum_payment_status=PAYMENT_STATUS_UNDER_REVIEW,
    ) | Q(annotated_full_payment_status=PAYMENT_STATUS_UNDER_REVIEW)


def filter_user_booking_status_bucket(queryset, status_bucket):
    normalized_bucket = normalize_user_booking_status_bucket(status_bucket)
    if normalized_bucket == USER_BOOKING_STATUS_BUCKET_ALL:
        return queryset

    under_review_q = build_user_booking_under_review_q()
    if normalized_bucket == USER_BOOKING_STATUS_BUCKET_UNDER_REVIEW:
        return queryset.filter(under_review_q)

    if normalized_bucket == USER_BOOKING_STATUS_BUCKET_ACTION_REQUIRED:
        return queryset.filter(
            effective_booking_status__in=(
                BOOKING_STATUS_HOLD,
                BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
                BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
            )
        ).exclude(under_review_q)

    if normalized_bucket == USER_BOOKING_STATUS_BUCKET_COMPLETED:
        return queryset.filter(effective_booking_status=BOOKING_STATUS_COMPLETED)

    if normalized_bucket == USER_BOOKING_STATUS_BUCKET_CANCELLED_EXPIRED:
        return queryset.filter(
            effective_booking_status__in=(BOOKING_STATUS_CANCELLED, BOOKING_STATUS_EXPIRED)
        )

    if normalized_bucket == USER_BOOKING_STATUS_BUCKET_IN_PROGRESS:
        return queryset.filter(
            effective_booking_status__in=(
                BOOKING_STATUS_READY_FOR_OPERATOR,
                BOOKING_STATUS_IN_FULFILLMENT,
                BOOKING_STATUS_READY_FOR_TRAVEL,
            )
        )

    return queryset


def apply_partner_visibility_filter(queryset, *, allow_hidden=False):
    if allow_hidden:
        return queryset

    return queryset.filter(
        Q(effective_booking_status__in=PARTNER_VISIBLE_BOOKING_STATUSES)
        | Q(issue_status__in=ISSUE_STATUSES)
    )


def build_partner_workflow_bucket_q(workflow_bucket):
    normalized_bucket = str(workflow_bucket or "").strip().upper()
    non_issue_q = ~Q(issue_status__in=ISSUE_STATUSES)

    if normalized_bucket == WORKFLOW_BUCKET_REPORTED:
        return Q(issue_status=ISSUE_STATUS_REPORTED)
    if normalized_bucket == WORKFLOW_BUCKET_ISSUES:
        return Q(issue_status__in=ISSUE_STATUSES)
    if normalized_bucket == WORKFLOW_BUCKET_VIEW_ONLY:
        return non_issue_q & Q(
            effective_booking_status__in=(
                BOOKING_STATUS_TRAVELER_DETAILS_PENDING,
                BOOKING_STATUS_AWAITING_FINAL_PAYMENT,
            )
        )
    if normalized_bucket == WORKFLOW_BUCKET_READY:
        return non_issue_q & Q(effective_booking_status=BOOKING_STATUS_READY_FOR_OPERATOR)
    if normalized_bucket == WORKFLOW_BUCKET_FULFILLMENT:
        return non_issue_q & Q(effective_booking_status=BOOKING_STATUS_IN_FULFILLMENT)
    if normalized_bucket == WORKFLOW_BUCKET_READY_FOR_TRAVEL:
        return non_issue_q & Q(effective_booking_status=BOOKING_STATUS_READY_FOR_TRAVEL)
    if normalized_bucket == WORKFLOW_BUCKET_COMPLETED:
        return non_issue_q & Q(effective_booking_status=BOOKING_STATUS_COMPLETED)
    if normalized_bucket == WORKFLOW_BUCKET_HISTORY:
        return non_issue_q & Q(
            effective_booking_status__in=(BOOKING_STATUS_CANCELLED, BOOKING_STATUS_EXPIRED)
        )
    return Q()


def filter_partner_booking_queryset(
    queryset,
    *,
    booking_status="",
    workflow_bucket="",
    booking_number="",
    allow_hidden=False,
):
    queryset = annotate_effective_booking_status(queryset)
    queryset = apply_partner_visibility_filter(queryset, allow_hidden=allow_hidden)

    normalized_booking_number = str(booking_number or "").strip().upper()
    if normalized_booking_number:
        queryset = queryset.filter(booking_number__startswith=normalized_booking_number)

    normalized_workflow_bucket = str(workflow_bucket or "").strip().upper()
    if normalized_workflow_bucket:
        return queryset.filter(build_partner_workflow_bucket_q(normalized_workflow_bucket))

    normalized_booking_status = str(booking_status or "").strip().upper()
    if normalized_booking_status:
        return queryset.filter(effective_booking_status=normalized_booking_status)

    return queryset


def annotate_resume_priority(queryset):
    return queryset.annotate(
        resume_priority=Case(
            When(effective_booking_status=BOOKING_STATUS_HOLD, then=Value(1)),
            When(effective_booking_status=BOOKING_STATUS_TRAVELER_DETAILS_PENDING, then=Value(2)),
            When(effective_booking_status=BOOKING_STATUS_AWAITING_FINAL_PAYMENT, then=Value(3)),
            When(effective_booking_status=BOOKING_STATUS_READY_FOR_OPERATOR, then=Value(4)),
            When(effective_booking_status=BOOKING_STATUS_IN_FULFILLMENT, then=Value(5)),
            When(effective_booking_status=BOOKING_STATUS_READY_FOR_TRAVEL, then=Value(6)),
            default=Value(99),
            output_field=IntegerField(),
        )
    )


def filter_active_capacity_queryset(queryset):
    queryset = annotate_effective_booking_status(queryset)
    return queryset.filter(effective_booking_status__in=BOOKING_STATUS_CAPACITY_SET)
