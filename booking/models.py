from django.db import models
import uuid
from django.utils import timezone
from common.models import UserProfile
from partners.models import PartnerProfile, HuzBasicDetail
from .statuses import BOOKING_STATUS_CHOICES, ISSUE_STATUS_CHOICES, ISSUE_STATUS_NONE, PAYMENT_STATUS_CHOICES


class Booking(models.Model):
    BOOKING_TYPE = BOOKING_STATUS_CHOICES

    # Define choices for payment type
    PAYMENT_TYPE = [
        ('Bank', 'bank'),
        ('Cheque', 'cheque'),
        ('Voucher', 'voucher'),
        ('Card', 'card')
    ]

    booking_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking_number = models.CharField(max_length=100, unique=True, null=True)
    adults = models.IntegerField()
    child = models.IntegerField(null=True, default=0)
    infants = models.IntegerField(null=True, default=0)
    sharing = models.CharField(max_length=50, null=True)
    quad = models.CharField(max_length=50, null=True)
    triple = models.CharField(max_length=50, null=True)
    double = models.CharField(max_length=50, null=True)
    single = models.CharField(max_length=50, null=True, default=0)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()
    total_price = models.FloatField(default=0.0)
    special_request = models.TextField(null=True)

    # Current status of the booking
    booking_status = models.CharField(max_length=40, choices=BOOKING_TYPE)
    issue_status = models.CharField(
        max_length=32,
        choices=ISSUE_STATUS_CHOICES,
        default=ISSUE_STATUS_NONE,
    )
    hold_expires_at = models.DateTimeField(null=True, blank=True)
    payment_correction_expires_at = models.DateTimeField(null=True, blank=True)
    status_changed_at = models.DateTimeField(default=timezone.now)

    # Time when the order was placed
    order_time = models.DateTimeField(default=timezone.now)
    # Payment type used for the booking
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPE)
    # Whether payment has been received or not
    is_payment_received = models.BooleanField(default=False)

    # Remarks made by the partner regarding the booking
    partner_remarks = models.TextField(null=True)

    # User who placed the order
    order_by = models.ForeignKey(UserProfile, related_name='order_by', on_delete=models.SET_NULL, null=True)
    # Partner to whom the order is assigned
    order_to = models.ForeignKey(PartnerProfile, related_name='order_to', on_delete=models.SET_NULL, null=True)
    # Token for the related travel package
    package_token = models.ForeignKey(HuzBasicDetail, related_name='package_token', on_delete=models.SET_NULL, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['booking_status', 'order_time'], name='booking_status_time_idx'),
            models.Index(fields=['order_by', 'booking_status'], name='booking_user_status_idx'),
            models.Index(fields=['order_to', 'booking_status'], name='booking_partner_status_idx'),
            models.Index(fields=['package_token', 'booking_status'], name='booking_package_status_idx'),
            models.Index(fields=['start_date'], name='booking_start_date_idx'),
            models.Index(fields=['order_by', 'order_time'], name='booking_user_time_idx'),
            models.Index(fields=['order_to', 'order_time'], name='booking_partner_time_idx'),
            models.Index(fields=['order_to', 'booking_number'], name='booking_partner_number_idx'),
        ]

    def __str__(self):
        # Return booking_id as string representation of the model
        return str(self.booking_id)


class BookingGroup(models.Model):
    group_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    label = models.CharField(max_length=100)
    sequence = models.PositiveIntegerField(default=1)
    notes = models.TextField(null=True, blank=True)
    booking = models.ForeignKey(
        Booking,
        related_name="booking_groups",
        on_delete=models.CASCADE,
    )

    class Meta:
        ordering = ["sequence", "label"]
        indexes = [
            models.Index(fields=["booking", "sequence"], name="booking_group_sequence_idx"),
        ]

    def __str__(self):
        return f"{self.booking.booking_number} - {self.label}"


class PassportValidity(models.Model):
    passport_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    traveler_sequence = models.PositiveIntegerField(default=1)
    traveler_type = models.CharField(max_length=100, null=True, blank=True)
    room_type = models.CharField(max_length=100, null=True, blank=True)
    first_name = models.CharField(max_length=100, null=True)
    middle_name = models.CharField(max_length=100, null=True)
    last_name = models.CharField(max_length=100, null=True)
    date_of_birth = models.DateTimeField(null=True)
    passport_number = models.CharField(max_length=20, null=True)
    passport_country = models.CharField(max_length=200, null=True)
    expiry_date = models.DateTimeField(null=True)
    user_passport = models.FileField(upload_to='passport_uploads', null=True, blank=True)
    user_photo = models.FileField(upload_to='passport_uploads', null=True, blank=True)
    booking_group = models.ForeignKey(
        BookingGroup,
        related_name="travelers",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    passport_for_booking_number = models.ForeignKey(Booking, related_name='passport_for_booking_number', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        # Return passport_id as string representation of the model
        return str(self.passport_id)


class Payment(models.Model):
    payment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Transaction number (proof of payment)
    transaction_number = models.CharField(max_length=500, null=True)
    transaction_type = models.CharField(max_length=500, null=True)
    # Photo of the transaction (e.g., receipt or proof of payment)
    transaction_photo = models.FileField(upload_to='payment_uploads', null=True, blank=True)
    # Amount of the transaction
    transaction_amount = models.FloatField()
    transaction_time = models.DateTimeField(default=timezone.now)
    payment_status = models.CharField(max_length=50, null=True, choices=PAYMENT_STATUS_CHOICES)
    review_message = models.TextField(null=True, blank=True)
    # Reference to the related booking
    booking_token = models.ForeignKey(Booking, related_name='booking_token', on_delete=models.SET_NULL, null=True)

    class Meta:
        indexes = [
            models.Index(fields=['booking_token', 'transaction_time'], name='payment_booking_time_idx'),
        ]
        constraints = [
            models.UniqueConstraint(
                fields=['transaction_number'],
                condition=models.Q(transaction_number__isnull=False) & ~models.Q(transaction_number=''),
                name='payment_transaction_number_unique',
            ),
        ]

    def __str__(self):
        return str(self.payment_id)


class BookingObjections(models.Model):
    objection_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    remarks_or_reason = models.CharField(max_length=250)
    client_remarks = models.CharField(max_length=250, null=True)
    required_document_for_objection = models.FileField(upload_to='user_images', null=True, blank=True)
    create_time = models.DateTimeField(default=timezone.now)
    objection_for_booking = models.ForeignKey(Booking, related_name='objection_for_booking', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.remarks_or_reason


class UserRequiredDocuments(models.Model):
    user_document_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Description or purpose of the document
    comment = models.CharField(max_length=100)
    user_document = models.FileField(upload_to='user_images', null=True, blank=True)
    document_type = models.CharField(max_length=100, null=True)
    create_time = models.DateTimeField(default=timezone.now)
    # Reference to the related booking
    user_document_for_booking_token = models.ForeignKey(Booking, related_name='user_document_for_booking_token', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.user_document_for_booking_token


class BookingDocuments(models.Model):
    DOCUMENT_SCOPE_BOOKING = "booking"
    DOCUMENT_SCOPE_GROUP = "group"
    DOCUMENT_SCOPE_TRAVELER = "traveler"
    DOCUMENT_SCOPE_CHOICES = [
        (DOCUMENT_SCOPE_BOOKING, DOCUMENT_SCOPE_BOOKING),
        (DOCUMENT_SCOPE_GROUP, DOCUMENT_SCOPE_GROUP),
        (DOCUMENT_SCOPE_TRAVELER, DOCUMENT_SCOPE_TRAVELER),
    ]

    document_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Description or purpose of the document
    document_for = models.CharField(max_length=100)
    document_category = models.CharField(max_length=100, null=True, blank=True)
    document_scope = models.CharField(
        max_length=20,
        choices=DOCUMENT_SCOPE_CHOICES,
        default=DOCUMENT_SCOPE_BOOKING,
    )
    document_title = models.CharField(max_length=150, null=True, blank=True)
    # Link to the document image
    document_link = models.FileField(upload_to='user_images', null=True, blank=True)
    create_time = models.DateTimeField(default=timezone.now)
    booking_group = models.ForeignKey(
        BookingGroup,
        related_name="documents",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    traveler = models.ForeignKey(
        PassportValidity,
        related_name="documents",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    # Reference to the related booking
    document_for_booking_token = models.ForeignKey(Booking, related_name='document_for_booking_token', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.document_for


class DocumentsStatus(models.Model):
    booking_status_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    is_user_passport_completed = models.BooleanField(default=False)
    is_visa_completed = models.BooleanField(default=False)
    is_airline_completed = models.BooleanField(default=False)
    is_airline_detail_completed = models.BooleanField(default=False, null=True)
    is_hotel_completed = models.BooleanField(default=False)
    is_transport_completed = models.BooleanField(default=False)
    # Reference to the related booking
    status_for_booking = models.ForeignKey(Booking, related_name='status_for_booking', on_delete=models.CASCADE, null=True)

    def __str__(self):
        return self.booking_status_id


class BookingAirlineDetail(models.Model):
    FLIGHT_DIRECTION_OUTBOUND = "outbound"
    FLIGHT_DIRECTION_RETURN = "return"
    FLIGHT_DIRECTION_CHOICES = (
        (FLIGHT_DIRECTION_OUTBOUND, "Outbound"),
        (FLIGHT_DIRECTION_RETURN, "Return"),
    )

    booking_airline_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flight_direction = models.CharField(
        max_length=20,
        choices=FLIGHT_DIRECTION_CHOICES,
        default=FLIGHT_DIRECTION_OUTBOUND,
    )
    flight_date = models.DateTimeField()
    flight_time = models.TimeField()
    flight_from = models.CharField(max_length=100)
    flight_to = models.CharField(max_length=100, null=True)
    # Ticket / operational fields (Phase 6)
    airline_name = models.CharField(max_length=150, null=True, blank=True)
    flight_number = models.CharField(max_length=20, null=True, blank=True)
    pnr = models.CharField(max_length=20, null=True, blank=True)
    baggage_note = models.TextField(null=True, blank=True)
    route_note = models.TextField(null=True, blank=True)
    # Reference to the related booking
    airline_for_booking = models.ForeignKey(Booking, related_name='airline_for_booking', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return str(self.booking_airline_id)


class BookingHotelFulfillment(models.Model):
    fulfillment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    city = models.CharField(max_length=100)
    hotel_name = models.CharField(max_length=150, null=True, blank=True)
    contact_name = models.CharField(max_length=100, null=True, blank=True)
    contact_phone = models.CharField(max_length=50, null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    shared_time = models.DateTimeField(default=timezone.now)
    package_hotel = models.ForeignKey(
        "partners.HuzHotelDetail",
        related_name="booking_fulfillments",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    hotel_for_booking = models.ForeignKey(
        Booking,
        related_name="hotel_fulfillments",
        on_delete=models.CASCADE,
    )

    class Meta:
        indexes = [
            models.Index(fields=["hotel_for_booking", "city"], name="booking_hotel_city_idx"),
        ]

    def __str__(self):
        return f"{self.hotel_for_booking.booking_number} - {self.city}"


class BookingTransportFulfillment(models.Model):
    MODE_NONE = "none"
    MODE_TICKET_ONLY = "ticket_only"
    MODE_DETAILS_ONLY = "details_only"
    MODE_DETAILS_AND_TICKET = "details_and_ticket"
    MODE_CHOICES = [
        (MODE_NONE, MODE_NONE),
        (MODE_TICKET_ONLY, MODE_TICKET_ONLY),
        (MODE_DETAILS_ONLY, MODE_DETAILS_ONLY),
        (MODE_DETAILS_AND_TICKET, MODE_DETAILS_AND_TICKET),
    ]

    transport_fulfillment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    transport_mode = models.CharField(max_length=32, choices=MODE_CHOICES, default=MODE_NONE)
    transport_name = models.CharField(max_length=100, null=True, blank=True)
    transport_type = models.CharField(max_length=100, null=True, blank=True)
    route_summary = models.CharField(max_length=250, null=True, blank=True)
    contact_name = models.CharField(max_length=100, null=True, blank=True)
    contact_phone = models.CharField(max_length=50, null=True, blank=True)
    ticket_reference = models.CharField(max_length=120, null=True, blank=True)
    note = models.TextField(null=True, blank=True)
    shared_time = models.DateTimeField(default=timezone.now)
    transport_for_booking = models.OneToOneField(
        Booking,
        related_name="transport_fulfillment",
        on_delete=models.CASCADE,
    )

    def __str__(self):
        return f"{self.transport_for_booking.booking_number} - {self.transport_mode}"


class TravelerIssue(models.Model):
    ISSUE_TYPE_REPORTED = "REPORTED"
    ISSUE_TYPE_RABBIT = "RABBIT"
    ISSUE_TYPE_CHOICES = [
        (ISSUE_TYPE_REPORTED, ISSUE_TYPE_REPORTED.lower()),
        (ISSUE_TYPE_RABBIT, ISSUE_TYPE_RABBIT.lower()),
    ]

    STATUS_OPEN = "open"
    STATUS_RESOLVED = "resolved"
    STATUS_CHOICES = [
        (STATUS_OPEN, STATUS_OPEN),
        (STATUS_RESOLVED, STATUS_RESOLVED),
    ]

    traveler_issue_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    booking = models.ForeignKey(
        Booking,
        related_name="traveler_issues",
        on_delete=models.CASCADE,
    )
    traveler = models.ForeignKey(
        PassportValidity,
        related_name="traveler_issues",
        on_delete=models.CASCADE,
    )
    issue_type = models.CharField(max_length=20, choices=ISSUE_TYPE_CHOICES, default=ISSUE_TYPE_REPORTED)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=STATUS_OPEN)
    notes = models.TextField(null=True, blank=True)
    created_by = models.ForeignKey(
        PartnerProfile,
        related_name="created_traveler_issues",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    resolved_by = models.ForeignKey(
        PartnerProfile,
        related_name="resolved_traveler_issues",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
    )
    created_at = models.DateTimeField(default=timezone.now)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        indexes = [
            models.Index(fields=["booking", "status"], name="trav_issue_book_stat_idx"),
            models.Index(fields=["traveler", "status"], name="trav_issue_trav_stat_idx"),
        ]

    def __str__(self):
        return f"{self.booking.booking_number} - {self.traveler_id} - {self.issue_type}"


class BookingRatingAndReview(models.Model):
    rating_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    huz_concierge = models.FloatField(default=0.0, null=True)
    huz_support = models.FloatField(default=0.0, null=True)
    huz_platform = models.FloatField(default=0.0, null=True)
    huz_service_quality = models.FloatField(default=0.0, null=True)
    huz_response_time = models.FloatField(default=0.0, null=True)
    huz_comment = models.TextField(null=True)
    partner_total_stars = models.FloatField(default=0.0)
    partner_comment = models.TextField(null=True)
    rating_time = models.DateTimeField(default=timezone.now)
    rating_by_user = models.ForeignKey(UserProfile, related_name='rating_by_user', on_delete=models.SET_NULL, null=True)
    # Reference to the related Partner profile
    rating_for_partner = models.ForeignKey(PartnerProfile, related_name='rating_for_partner', on_delete=models.SET_NULL, null=True)
    # Reference to the related booking
    rating_for_booking = models.ForeignKey(Booking, related_name='rating_for_booking', on_delete=models.SET_NULL, null=True)
    # Reference to the related Package detail
    rating_for_package = models.ForeignKey(HuzBasicDetail, related_name='rating_for_package', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.rating_id


class BookingComplaints(models.Model):
    complaint_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    complaint_ticket = models.CharField(max_length=20, null=True)
    complaint_title = models.CharField(max_length=100, null=True)
    complaint_message = models.TextField(null=True)
    audio_message = models.FileField(upload_to='user_images', null=True, blank=True)
    complaint_attachment = models.FileField(upload_to='user_images', null=True, blank=True)
    complaint_status = models.CharField(max_length=100, null=True)
    complaint_time = models.DateTimeField(default=timezone.now)
    response_message = models.TextField(null=True)
    # Reference to the related user profile
    complaint_by_user = models.ForeignKey(UserProfile, related_name='complaint_by_user', on_delete=models.CASCADE)
    # Reference to the related Partner profile
    complaint_for_partner = models.ForeignKey(PartnerProfile, related_name='complaint_for_partner', on_delete=models.CASCADE)
    # Reference to the related Package detail
    complaint_for_package = models.ForeignKey(HuzBasicDetail, related_name='complaint_for_package', on_delete=models.CASCADE)
    # Reference to the related booking
    complaint_for_booking = models.ForeignKey(Booking, related_name='complaint_for_booking', on_delete=models.CASCADE)

    def __str__(self):
        return self.complaint_id


class PartnersBookingPayment(models.Model):
    payment_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    receivable_amount = models.FloatField(default=0.0)
    pending_amount = models.FloatField(default=0.0)
    processed_amount = models.FloatField(default=0.0)
    processed_date = models.DateTimeField(null=True)
    create_date = models.DateTimeField(default=timezone.now)
    payment_status = models.CharField(max_length=20, null=True)
    # Reference to the related Partner profile
    payment_for_partner = models.ForeignKey(PartnerProfile, related_name='payment_for_partner', on_delete=models.CASCADE)
    # Reference to the related Package detail
    payment_for_package = models.ForeignKey(HuzBasicDetail, related_name='payment_for_package', on_delete=models.CASCADE)
    # Reference to the related booking
    payment_for_booking = models.ForeignKey(Booking, related_name='payment_for_booking', on_delete=models.CASCADE)

    def __str__(self):
        return self.payment_id


class BookingRequest(models.Model):
    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_ticket = models.CharField(max_length=20, null=True)
    request_title = models.CharField(max_length=100, null=True)
    request_message = models.TextField(null=True)
    request_attachment = models.FileField(upload_to='user_images', null=True, blank=True)
    request_status = models.CharField(max_length=100, null=True)
    inProgress_message = models.TextField(null=True)
    final_response_message = models.TextField(null=True)
    request_by_user = models.ForeignKey(UserProfile, related_name='request_by_user', on_delete=models.CASCADE)
    request_for_package = models.ForeignKey(HuzBasicDetail, related_name='request_for_package', on_delete=models.CASCADE)
    request_for_partner = models.ForeignKey(PartnerProfile, related_name='request_for_partner', on_delete=models.CASCADE)
    request_for_booking = models.ForeignKey(Booking, related_name='request_for_booking', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return self.request_id
