from django.db import models
import uuid
from django.utils import timezone
from common.models import UserProfile
from partners.models import PartnerProfile, HuzBasicDetail


class Booking(models.Model):
    # Define choices for booking status
    BOOKING_TYPE = [
        ('Initialize', 'initialize'),
        ('Passport_Validation', 'passport_Validation'),
        ('Paid', 'paid'),
        ('Confirm', 'confirm'),
        ('Pending', 'pending'),
        ('Active', 'active'),
        ('Completed', 'completed'),
        ('Closed', 'closed'),
        ('Objection', 'objection'),
        ('Report', 'report'),
        ('Cancel', 'cancel'),
        ('Rejected', 'rejected')
    ]

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
    booking_status = models.CharField(max_length=20, choices=BOOKING_TYPE)

    # Time when the order was placed
    order_time = models.DateTimeField(default=timezone.now)
    # Payment type used for the booking
    payment_type = models.CharField(max_length=20, choices=PAYMENT_TYPE)
    # Whether payment has been received or not
    is_payment_received = models.BooleanField(default=False)

    is_check_in_makkah = models.BooleanField(default=False)
    is_check_in_madinah = models.BooleanField(default=False)
    # Remarks made by the partner regarding the booking
    partner_remarks = models.TextField(null=True)

    # User who placed the order
    order_by = models.ForeignKey(UserProfile, related_name='order_by', on_delete=models.SET_NULL, null=True)
    # Partner to whom the order is assigned
    order_to = models.ForeignKey(PartnerProfile, related_name='order_to', on_delete=models.SET_NULL, null=True)
    # Token for the related travel package
    package_token = models.ForeignKey(HuzBasicDetail, related_name='package_token', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        # Return booking_id as string representation of the model
        return str(self.booking_id)


class PassportValidity(models.Model):
    passport_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    first_name = models.CharField(max_length=100, null=True)
    middle_name = models.CharField(max_length=100, null=True)
    last_name = models.CharField(max_length=100, null=True)
    date_of_birth = models.DateTimeField(null=True)
    passport_number = models.CharField(max_length=20, null=True)
    passport_country = models.CharField(max_length=200, null=True)
    expiry_date = models.DateTimeField(null=True)
    report_rabbit = models.BooleanField(null=True, default=False)
    user_passport = models.ImageField(upload_to='user_images', null=True, blank=True)
    user_photo = models.ImageField(upload_to='user_images', null=True, blank=True)
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
    transaction_photo = models.ImageField(upload_to='user_images', null=True, blank=True)
    # Amount of the transaction
    transaction_amount = models.FloatField()
    transaction_time = models.DateTimeField(default=timezone.now)
    payment_status = models.CharField(max_length=50, null=True)
    # Reference to the related booking
    booking_token = models.ForeignKey(Booking, related_name='booking_token', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return str(self.payment_id)


class BookingObjections(models.Model):
    objection_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    remarks_or_reason = models.CharField(max_length=250)
    client_remarks = models.CharField(max_length=250, null=True)
    required_document_for_objection = models.ImageField(upload_to='user_images', null=True, blank=True)
    create_time = models.DateTimeField(default=timezone.now)
    objection_for_booking = models.ForeignKey(Booking, related_name='objection_for_booking', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.remarks_or_reason


class UserRequiredDocuments(models.Model):
    user_document_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Description or purpose of the document
    comment = models.CharField(max_length=100)
    user_document = models.ImageField(upload_to='user_images', null=True, blank=True)
    document_type = models.CharField(max_length=100, null=True)
    create_time = models.DateTimeField(default=timezone.now)
    # Reference to the related booking
    user_document_for_booking_token = models.ForeignKey(Booking, related_name='user_document_for_booking_token', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.user_document_for_booking_token


class BookingDocuments(models.Model):
    document_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Description or purpose of the document
    document_for = models.CharField(max_length=100)
    # Link to the document image
    document_link = models.ImageField(upload_to='user_images', null=True, blank=True)
    create_time = models.DateTimeField(default=timezone.now)
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
    booking_airline_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    flight_date = models.DateTimeField()
    flight_time = models.TimeField()
    flight_from = models.CharField(max_length=100)
    flight_to = models.CharField(max_length=100, null=True)
    # Reference to the related booking
    airline_for_booking = models.ForeignKey(Booking, related_name='airline_for_booking', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.booking_airline_id


class BookingHotelAndTransport(models.Model):
    hotel_or_transport_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    jeddah_name = models.CharField(max_length=100, null=True)
    jeddah_number = models.CharField(max_length=20, null=True)
    mecca_name = models.CharField(max_length=100)
    mecca_number = models.CharField(max_length=20)
    madinah_name = models.CharField(max_length=100)
    madinah_number = models.CharField(max_length=20)
    comment_1 = models.TextField(null=True)
    comment_2 = models.TextField(null=True)
    # e.g Hotel or Transport
    detail_for = models.CharField(max_length=20, null=True)
    shared_time = models.DateTimeField(default=timezone.now)
    # Reference to the related booking
    hotel_or_transport_for_booking = models.ForeignKey(Booking, related_name='hotel_or_transport_for_booking', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return self.hotel_or_transport_id


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


class CustomPackages(models.Model):
    # Define choices for booking status
    PACKAGE_STATUS = [
        ('Initialize', 'initialize'),
        ('Assigned', 'assigned')
    ]

    request_id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_number = models.CharField(max_length=100, unique=True, null=True)
    adults = models.IntegerField()
    child = models.IntegerField(null=True, default=0)
    infants = models.IntegerField(null=True, default=0)
    depart_city = models.CharField(max_length=100)
    days_in_makkah = models.IntegerField(null=True, default=0)
    days_in_madinah = models.IntegerField(null=True, default=0)
    start_date = models.DateTimeField()
    end_date = models.DateTimeField()

    is_visa_required = models.BooleanField(default=False)

    airline_name = models.CharField(max_length=100, null=True)
    ticket_type = models.CharField(max_length=100, null=True)

    makkah_hotel_type = models.CharField(max_length=20)
    makkah_hotel_name = models.CharField(max_length=100, null=True)
    makkah_occupany_type = models.CharField(max_length=100, null=True)

    madinah_hotel_type = models.CharField(max_length=20)
    madinah_hotel_name = models.CharField(max_length=100, null=True)
    madinah_occupany_type = models.CharField(max_length=100, null=True)

    transport_type = models.CharField(max_length=20, null=True)
    transport_route = models.CharField(max_length=200, null=True)

    meals = models.CharField(max_length=200, null=True)
    ziyarah = models.TextField(null=True)

    special_request = models.TextField(null=True)
    booking_status = models.CharField(max_length=20, choices=PACKAGE_STATUS)
    request_time = models.DateTimeField(default=timezone.now)
    request_by = models.ForeignKey(UserProfile, related_name='request_by', on_delete=models.SET_NULL, null=True)

    def __str__(self):
        return str(self.request_id)

