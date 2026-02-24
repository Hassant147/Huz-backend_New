from rest_framework import serializers
from .models import CustomPackages, Booking, Payment, BookingRequest, PassportValidity, BookingObjections, PartnersBookingPayment, BookingDocuments, DocumentsStatus, BookingAirlineDetail, BookingHotelAndTransport, BookingRatingAndReview, BookingComplaints, UserRequiredDocuments
from common.models import UserProfile, MailingDetail
from common.serializers import MailingDetailSerializer
from partners.models import PartnerProfile, HuzBasicDetail, BusinessProfile, PartnerMailingDetail, HuzAirlineDetail
from partners.serializers import ShortBusinessSerializer, PartnerMailingDetailSerializer, HuzAirlineSerializer


def _get_prefetched_items(instance, relation_name):
    prefetched_cache = getattr(instance, '_prefetched_objects_cache', {})
    if relation_name in prefetched_cache:
        return prefetched_cache.get(relation_name) or []

    relation = getattr(instance, relation_name, None)
    if relation is None:
        return []

    try:
        return list(relation.all())
    except Exception:
        return []


def _get_first_prefetched_item(instance, relation_name):
    prefetched_items = _get_prefetched_items(instance, relation_name)
    return prefetched_items[0] if prefetched_items else None


def get_company_detail(obj):
    if not obj.order_to or obj.order_to.partner_type != "Company":
        return None

    prefetched_company = _get_first_prefetched_item(obj.order_to, 'company_of_partner')
    if prefetched_company:
        return ShortBusinessSerializer(prefetched_company).data

    if obj.order_to.partner_type == "Company":
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=obj.order_to.partner_id)
            return ShortBusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None
    else:
        return None


def get_user_address_detail(obj):
    if not obj.order_by:
        return None

    prefetched_address = _get_first_prefetched_item(obj.order_by, 'mailing_session')
    if prefetched_address:
        return MailingDetailSerializer(prefetched_address).data

    try:
        address_detail = MailingDetail.objects.get(mailing_session=obj.order_by)
        return MailingDetailSerializer(address_detail).data
    except MailingDetail.DoesNotExist:
        return None


def get_partner_address_detail(obj):
    if not obj.order_to:
        return None

    prefetched_address = _get_first_prefetched_item(obj.order_to, 'mailing_of_partner')
    if prefetched_address:
        return PartnerMailingDetailSerializer(prefetched_address).data

    try:
        address_detail = PartnerMailingDetail.objects.get(mailing_of_partner=obj.order_to)
        return PartnerMailingDetailSerializer(address_detail).data
    except PartnerMailingDetail.DoesNotExist:
        return None


def get_booking_objections(obj):
    prefetched_objections = _get_prefetched_items(obj, 'objection_for_booking')
    if prefetched_objections:
        return BookingObjectionsSerializer(prefetched_objections, many=True).data

    objections_detail = BookingObjections.objects.filter(objection_for_booking=obj.booking_id)
    return BookingObjectionsSerializer(objections_detail, many=True).data


def get_passport_validity(obj):
    prefetched_passports = _get_prefetched_items(obj, 'passport_for_booking_number')
    if prefetched_passports:
        return PassportValiditySerializer(prefetched_passports, many=True).data

    passport_validity = PassportValidity.objects.filter(passport_for_booking_number=obj.booking_id)
    return PassportValiditySerializer(passport_validity, many=True).data


def get_payment_detail(obj):
    prefetched_payments = _get_prefetched_items(obj, 'booking_token')
    if prefetched_payments:
        return PaymentSerializer(prefetched_payments, many=True).data

    payment_paid = Payment.objects.filter(booking_token=obj.booking_id)
    return PaymentSerializer(payment_paid, many=True).data


class ShortBookingSerializer(serializers.ModelSerializer):
    # Partner Section
    partner_session_token = serializers.CharField(source='order_to.partner_session_token', read_only=True)
    # User Section
    user_session_token = serializers.CharField(source='order_by.session_token', read_only=True)
    user_fullname = serializers.CharField(source='order_by.name', read_only=True)
    user_country_code = serializers.CharField(source='order_by.country_code', read_only=True)
    user_phone_number = serializers.CharField(source='order_by.phone_number', read_only=True)
    user_email = serializers.CharField(source='order_by.email', read_only=True)
    user_photo = serializers.CharField(source='order_by.user_photo', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    # Package Section
    huz_token = serializers.CharField(source='package_token.huz_token', read_only=True)
    package_type = serializers.CharField(source='package_token.package_type', read_only=True)
    package_name = serializers.CharField(source='package_token.package_name', read_only=True)
    package_cost = serializers.CharField(source='package_token.package_base_cost', read_only=True)
    mecca_nights = serializers.CharField(source='package_token.mecca_nights', read_only=True)
    madinah_nights = serializers.CharField(source='package_token.madinah_nights', read_only=True)
    is_visa_included = serializers.CharField(source='package_token.is_visa_included', read_only=True)
    is_airport_reception_included = serializers.CharField(source='package_token.is_airport_reception_included', read_only=True)
    is_tour_guide_included = serializers.CharField(source='package_token.is_tour_guide_included', read_only=True)
    is_insurance_included = serializers.CharField(source='package_token.is_insurance_included', read_only=True)
    is_breakfast_included = serializers.CharField(source='package_token.is_breakfast_included', read_only=True)
    is_lunch_included = serializers.CharField(source='package_token.is_lunch_included', read_only=True)
    is_dinner_included = serializers.CharField(source='package_token.is_dinner_included', read_only=True)
    # Payment Verified or not
    payment_detail = serializers.SerializerMethodField()
    passport_validity_detail = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = (
            'booking_number', 'adults', 'child', 'infants', 'start_date', 'end_date', 'sharing', 'quad', 'triple',
            'double', 'single', 'total_price', 'special_request', 'booking_status', 'order_time', 'payment_type',
            'is_payment_received',

            'partner_session_token',

            'user_session_token', 'user_fullname', 'user_country_code', 'user_phone_number', 'user_email',
            'user_photo', 'user_address_detail',

            'huz_token', 'package_type', 'package_name', 'package_cost', 'mecca_nights', 'madinah_nights',
            'is_visa_included', 'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included',
            'is_breakfast_included', 'is_lunch_included', 'is_dinner_included',

            'passport_validity_detail',

            'payment_detail',
            'order_by', 'order_to', 'package_token'
                  )

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_user_address_detail(self, obj):
        return get_user_address_detail(obj)

    def get_payment_detail(self, obj):
        return get_payment_detail(obj)

    def get_passport_validity_detail(self, obj):
        return get_passport_validity(obj)


class DetailBookingSerializer(serializers.ModelSerializer):
    # Partner Section
    partner_session_token = serializers.CharField(source='order_to.partner_session_token', read_only=True)
    partner_email = serializers.CharField(source='order_to.email', read_only=True)
    partner_name = serializers.CharField(source='order_to.name', read_only=True)
    partner_username = serializers.CharField(source='order_to.user_name', read_only=True)
    company_detail = serializers.SerializerMethodField()
    partner_address_detail = serializers.SerializerMethodField()
    # User Section
    user_session_token = serializers.CharField(source='order_by.session_token', read_only=True)
    user_fullName = serializers.CharField(source='order_by.name', read_only=True)
    user_email = serializers.CharField(source='order_by.email', read_only=True)
    user_country_code = serializers.CharField(source='order_by.country_code', read_only=True)
    user_phone_number = serializers.CharField(source='order_by.phone_number', read_only=True)
    user_photo = serializers.CharField(source='order_by.user_photo', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    # Package Detail
    huz_token = serializers.CharField(source='package_token.huz_token', read_only=True)
    package_type = serializers.CharField(source='package_token.package_type', read_only=True)
    package_name = serializers.CharField(source='package_token.package_name', read_only=True)
    package_cost = serializers.CharField(source='package_token.package_base_cost', read_only=True)
    mecca_nights = serializers.CharField(source='package_token.mecca_nights', read_only=True)
    madinah_nights = serializers.CharField(source='package_token.madinah_nights', read_only=True)
    is_visa_included = serializers.CharField(source='package_token.is_visa_included', read_only=True)
    is_airport_reception_included = serializers.CharField(source='package_token.is_airport_reception_included', read_only=True)
    is_tour_guide_included = serializers.CharField(source='package_token.is_tour_guide_included', read_only=True)
    is_insurance_included = serializers.CharField(source='package_token.is_insurance_included', read_only=True)
    is_breakfast_included = serializers.CharField(source='package_token.is_breakfast_included', read_only=True)
    is_lunch_included = serializers.CharField(source='package_token.is_lunch_included', read_only=True)
    is_dinner_included = serializers.CharField(source='package_token.is_dinner_included', read_only=True)

    cost_for_sharing = serializers.CharField(source='package_token.cost_for_sharing', read_only=True)
    cost_for_quad = serializers.CharField(source='package_token.cost_for_quad', read_only=True)
    cost_for_triple = serializers.CharField(source='package_token.cost_for_triple', read_only=True)
    cost_for_double = serializers.CharField(source='package_token.cost_for_double', read_only=True)
    cost_for_single = serializers.CharField(source='package_token.cost_for_single', read_only=True)

    airline_detail = serializers.SerializerMethodField()
    booking_documents_status = serializers.SerializerMethodField()
    booking_documents = serializers.SerializerMethodField()
    user_documents = serializers.SerializerMethodField()
    booking_airline_details = serializers.SerializerMethodField()
    booking_hotel_and_transport_details = serializers.SerializerMethodField()
    booking_rating = serializers.SerializerMethodField()
    payment_detail = serializers.SerializerMethodField()
    booking_objections = serializers.SerializerMethodField()
    passport_validity_detail = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = (
            'booking_id', 'booking_number', 'adults', 'child', 'infants', 'start_date', 'end_date', 'sharing', 'quad',
            'triple', 'double', 'single', 'total_price',
            'special_request', 'booking_status', 'order_time', 'payment_type', 'is_payment_received', 'partner_remarks',

            'partner_session_token', 'partner_email', 'partner_name', 'partner_username', 'company_detail',
            'partner_address_detail',

            'user_session_token', 'user_fullName', 'user_country_code', 'user_phone_number', 'user_email',
            'user_photo', 'user_address_detail',

            'airline_detail',

            'huz_token', 'package_type', 'package_name', 'package_cost', 'mecca_nights', 'madinah_nights',
            'is_visa_included', 'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included',
            'is_breakfast_included', 'is_lunch_included', 'is_dinner_included',
            'cost_for_sharing', 'cost_for_quad', 'cost_for_triple', 'cost_for_double', 'cost_for_single',
            'payment_detail', 'booking_objections', 'is_check_in_makkah', 'is_check_in_madinah',

            'passport_validity_detail',
            'booking_documents_status',  'user_documents',
            'booking_documents', 'booking_airline_details', 'booking_hotel_and_transport_details', 'booking_rating'
                  )

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_airline_detail(self, obj):
        if not obj.package_token:
            return []

        prefetched_airlines = _get_prefetched_items(obj.package_token, 'airline_for_package')
        if prefetched_airlines:
            return HuzAirlineSerializer(prefetched_airlines, many=True).data

        airline = HuzAirlineDetail.objects.filter(airline_for_package=obj.package_token)
        return HuzAirlineSerializer(airline, many=True).data

    def get_passport_validity_detail(self, obj):
        return get_passport_validity(obj)

    def get_partner_address_detail(self, obj):
        return get_partner_address_detail(obj)

    def get_user_address_detail(self, obj):
        return get_user_address_detail(obj)

    def get_booking_objections(self, obj):
        return get_booking_objections(obj)

    def get_booking_documents_status(self, obj):
        prefetched_documents = _get_prefetched_items(obj, 'status_for_booking')
        if prefetched_documents:
            return DocumentsStatusSerializer(prefetched_documents, many=True).data

        documents = DocumentsStatus.objects.filter(status_for_booking=obj.booking_id)
        return DocumentsStatusSerializer(documents, many=True).data

    def get_user_documents(self, obj):
        prefetched_documents = _get_prefetched_items(obj, 'user_document_for_booking_token')
        if prefetched_documents:
            return UserRequiredBookingDocumentsSerializer(prefetched_documents, many=True).data

        documents = UserRequiredDocuments.objects.filter(user_document_for_booking_token=obj.booking_id)
        return UserRequiredBookingDocumentsSerializer(documents, many=True).data

    def get_booking_documents(self, obj):
        prefetched_documents = _get_prefetched_items(obj, 'document_for_booking_token')
        if prefetched_documents:
            return BookingDocumentsSerializer(prefetched_documents, many=True).data

        documents = BookingDocuments.objects.filter(document_for_booking_token=obj.booking_id)
        return BookingDocumentsSerializer(documents, many=True).data

    def get_booking_airline_details(self, obj):
        prefetched_airline_details = _get_prefetched_items(obj, 'airline_for_booking')
        if prefetched_airline_details:
            return BookingAirlineSerializer(prefetched_airline_details, many=True).data

        airline = BookingAirlineDetail.objects.filter(airline_for_booking=obj.booking_id)
        return BookingAirlineSerializer(airline, many=True).data

    def get_booking_hotel_and_transport_details(self, obj):
        prefetched_details = _get_prefetched_items(obj, 'hotel_or_transport_for_booking')
        if prefetched_details:
            return BookingHotelOrTransportSerializer(prefetched_details, many=True).data

        airline = BookingHotelAndTransport.objects.filter(hotel_or_transport_for_booking=obj.booking_id)
        return BookingHotelOrTransportSerializer(airline, many=True).data



    def get_booking_rating(self, obj):
        prefetched_ratings = _get_prefetched_items(obj, 'rating_for_booking')
        if prefetched_ratings:
            return BookingRatingAndReviewSerializer(prefetched_ratings, many=True).data

        airline = BookingRatingAndReview.objects.filter(rating_for_booking=obj.booking_id)
        return BookingRatingAndReviewSerializer(airline, many=True).data

    def get_payment_detail(self, obj):
        return get_payment_detail(obj)


class AdminPaidBookingSerializer(serializers.ModelSerializer):
    # Partner Section
    partner_session_token = serializers.CharField(source='order_to.partner_session_token', read_only=True)
    partner_email = serializers.CharField(source='order_to.email', read_only=True)
    partner_name = serializers.CharField(source='order_to.name', read_only=True)
    partner_username = serializers.CharField(source='order_to.user_name', read_only=True)
    company_detail = serializers.SerializerMethodField()
    partner_address_detail = serializers.SerializerMethodField()

    # User Section
    user_session_token = serializers.CharField(source='order_by.session_token', read_only=True)
    user_fullName = serializers.CharField(source='order_by.name', read_only=True)
    user_email = serializers.CharField(source='order_by.email', read_only=True)
    user_country_code = serializers.CharField(source='order_by.country_code', read_only=True)
    user_phone_number = serializers.CharField(source='order_by.phone_number', read_only=True)
    user_photo = serializers.CharField(source='order_by.user_photo', read_only=True)

    # Package Detail
    huz_token = serializers.CharField(source='package_token.huz_token', read_only=True)
    package_type = serializers.CharField(source='package_token.package_type', read_only=True)
    package_name = serializers.CharField(source='package_token.package_name', read_only=True)
    package_cost = serializers.CharField(source='package_token.package_base_cost', read_only=True)
    mecca_nights = serializers.CharField(source='package_token.mecca_nights', read_only=True)
    madinah_nights = serializers.CharField(source='package_token.madinah_nights', read_only=True)
    is_visa_included = serializers.CharField(source='package_token.is_visa_included', read_only=True)
    is_airport_reception_included = serializers.CharField(source='package_token.is_airport_reception_included', read_only=True)
    is_tour_guide_included = serializers.CharField(source='package_token.is_tour_guide_included', read_only=True)
    is_insurance_included = serializers.CharField(source='package_token.is_insurance_included', read_only=True)
    is_breakfast_included = serializers.CharField(source='package_token.is_breakfast_included', read_only=True)
    is_lunch_included = serializers.CharField(source='package_token.is_lunch_included', read_only=True)
    is_dinner_included = serializers.CharField(source='package_token.is_dinner_included', read_only=True)

    payment_detail = serializers.SerializerMethodField()

    class Meta:
        model = Booking
        fields = (
            'booking_number', 'adults', 'child', 'infants', 'start_date', 'end_date', 'sharing', 'quad', 'triple',
            'double', 'single', 'total_price', 'special_request', 'booking_status', 'order_time', 'payment_type',
            'is_payment_received',

            'partner_session_token', 'partner_email', 'partner_name', 'partner_username',
            'company_detail', 'partner_address_detail',

            'user_session_token', 'user_fullName', 'user_email', 'user_country_code', 'user_phone_number',
            'user_photo',

            'huz_token', 'package_type', 'package_name', 'package_cost', 'mecca_nights', 'madinah_nights',
            'is_visa_included', 'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included',
            'is_breakfast_included', 'is_lunch_included', 'is_dinner_included',

            'payment_detail',
        )

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_partner_address_detail(self, obj):
        return get_partner_address_detail(obj)

    def get_payment_detail(self, obj):
        return get_payment_detail(obj)


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['payment_id', 'transaction_number', 'transaction_photo', 'transaction_amount', 'transaction_time', 'transaction_type', 'payment_status']


class BookingObjectionsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingObjections
        fields = ['objection_id', 'remarks_or_reason', 'client_remarks', 'required_document_for_objection', 'create_time']


class UserRequiredBookingDocumentsSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserRequiredDocuments
        fields = ['user_document_id', 'comment', 'user_document', 'document_type',
                  'user_document_for_booking_token']


class BookingDocumentsSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingDocuments
        fields = ['document_id', 'document_for', 'document_link']


class DocumentsStatusSerializer(serializers.ModelSerializer):
    class Meta:
        model = DocumentsStatus
        fields = ['is_user_passport_completed', 'is_visa_completed', 'is_airline_detail_completed',
                  'is_airline_completed', 'is_hotel_completed', 'is_transport_completed']


class BookingAirlineSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingAirlineDetail
        fields = ['booking_airline_id', 'flight_date', 'flight_time', 'flight_from', 'flight_to']


class BookingHotelOrTransportSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingHotelAndTransport
        fields = ['hotel_or_transport_id', 'detail_for', 'jeddah_name', 'jeddah_number', 'mecca_name',
                  'mecca_number', 'madinah_name', 'madinah_number', 'comment_1', 'comment_2',
                  'shared_time']


class BookingRatingAndReviewSerializer(serializers.ModelSerializer):
    class Meta:
        model = BookingRatingAndReview
        fields = ['huz_concierge', 'huz_support', 'huz_platform', 'huz_service_quality', 'huz_response_time',
                  'huz_comment', 'partner_total_stars', 'partner_comment', 'rating_time']


class PassportValiditySerializer(serializers.ModelSerializer):
    class Meta:
        model = PassportValidity
        fields = ['passport_id', 'first_name', 'middle_name', 'last_name', 'date_of_birth', 'passport_number',
                  'passport_country', 'expiry_date', 'user_passport', 'user_photo', 'report_rabbit']


class PartnerRatingSerializer(serializers.ModelSerializer):
    user_photo = serializers.CharField(source='rating_by_user.user_photo', read_only=True)
    user_fullName = serializers.CharField(source='rating_by_user.name', read_only=True)
    user_address_detail = serializers.SerializerMethodField()

    class Meta:
        model = BookingRatingAndReview
        fields = ['partner_total_stars', 'partner_comment', 'rating_time', 'user_fullName', 'user_photo', 'user_address_detail']

    def get_user_address_detail(self, obj):
        try:
            company_detail = MailingDetail.objects.get(mailing_session=obj.rating_by_user.user_id)
            return MailingDetailSerializer(company_detail).data
        except MailingDetail.DoesNotExist:
            return None


class BookingComplaintsSerializer(serializers.ModelSerializer):
    user_photo = serializers.CharField(source='complaint_by_user.user_photo', read_only=True)
    user_fullName = serializers.CharField(source='complaint_by_user.name', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    partner_contact_detail = serializers.SerializerMethodField()
    package_type = serializers.CharField(source='complaint_for_package.package_type', read_only=True)
    package_name = serializers.CharField(source='complaint_for_package.package_name', read_only=True)
    package_cost = serializers.CharField(source='complaint_for_package.package_base_cost', read_only=True)
    booking_number = serializers.CharField(source='complaint_for_booking.booking_number', read_only=True)

    class Meta:
        model = BookingComplaints
        fields = ['complaint_id', 'complaint_ticket', 'complaint_title', 'complaint_message', 'audio_message',
                  'complaint_attachment', 'complaint_status',
                  'complaint_time', 'response_message', 'user_fullName', 'user_photo', 'user_address_detail',
                  'package_type', 'package_name', 'package_cost', 'booking_number', 'partner_contact_detail']

    def get_user_address_detail(self, obj):
        try:
            company_detail = MailingDetail.objects.get(mailing_session=obj.complaint_by_user)
            return MailingDetailSerializer(company_detail).data
        except MailingDetail.DoesNotExist:
            return None

    def get_partner_contact_detail(self, obj):
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=obj.complaint_for_partner)
            return ShortBusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None


class PartnersBookingPaymentSerializer(serializers.ModelSerializer):
    booking_number = serializers.CharField(source='payment_for_booking.booking_number', read_only=True)
    package_type = serializers.CharField(source='payment_for_package.package_type', read_only=True)
    package_name = serializers.CharField(source='payment_for_package.package_name', read_only=True)
    partner_name = serializers.CharField(source='payment_for_partner.name', read_only=True)
    partner_session_token = serializers.CharField(source='payment_for_partner.partner_session_token', read_only=True)

    partner_contact_detail = serializers.SerializerMethodField()

    class Meta:
        model = PartnersBookingPayment
        fields = ['package_type', 'package_name', 'booking_number', 'payment_status', 'receivable_amount', 'pending_amount', 'processed_amount', 'processed_date', 'create_date', 'partner_contact_detail', 'partner_name', 'partner_session_token']

    def get_partner_contact_detail(self, obj):
        if obj.payment_for_partner:
            prefetched_company = _get_first_prefetched_item(obj.payment_for_partner, 'company_of_partner')
            if prefetched_company:
                return ShortBusinessSerializer(prefetched_company).data

        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=obj.payment_for_partner)
            return ShortBusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None


class BookingRequestSerializer(serializers.ModelSerializer):
    user_photo = serializers.CharField(source='request_by_user.user_photo', read_only=True)
    user_fullName = serializers.CharField(source='request_by_user.name', read_only=True)
    user_address_detail = serializers.SerializerMethodField()
    partner_contact_detail = serializers.SerializerMethodField()
    package_type = serializers.CharField(source='request_for_package.package_type', read_only=True)
    package_name = serializers.CharField(source='request_for_package.package_name', read_only=True)
    package_cost = serializers.CharField(source='request_for_package.package_base_cost', read_only=True)
    booking_number = serializers.CharField(source='request_for_booking.booking_number', read_only=True)

    class Meta:
        model = BookingRequest
        fields = ['request_id', 'request_ticket', 'request_title', 'request_message', 'request_attachment',
                  'request_status', 'inProgress_message', 'final_response_message', 'created_at', 'updated_at',
                  'user_fullName', 'user_photo', 'user_address_detail',
                  'package_type', 'package_name', 'package_cost', 'booking_number', 'partner_contact_detail']

    def get_user_address_detail(self, obj):
        try:
            company_detail = MailingDetail.objects.get(mailing_session=obj.request_by_user)
            return MailingDetailSerializer(company_detail).data
        except MailingDetail.DoesNotExist:
            return None

    def get_partner_contact_detail(self, obj):
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=obj.request_for_partner)
            return ShortBusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None


class CustomPackageSerializer(serializers.ModelSerializer):
    class Meta:
        model = CustomPackages
        fields = '__all__'

    def validate(self, attrs):
        # You can add any custom validation here if needed
        if attrs['adults'] < 1:
            raise serializers.ValidationError("Number of adults must be at least 1.")
        return attrs
