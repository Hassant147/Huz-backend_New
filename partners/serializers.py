from rest_framework import serializers
from django.db.models import Sum, Count
from booking.models import BookingRatingAndReview
import re
from .models import (PartnerProfile, Wallet, PartnerServices, IndividualProfile, BusinessProfile, PartnerMailingDetail,
                     HuzBasicDetail, HuzAirlineDetail, HuzTransportDetail, HuzHotelDetail, HuzZiyarahDetail,
                     PartnerBankAccount, PartnerWithdraw, PartnerTransactionHistory)


def get_type_and_detail(partner_profile):
    if partner_profile.partner_type == "Individual":
        try:
            identity_detail = IndividualProfile.objects.get(individual_profile_of_partner=partner_profile.partner_id)
            return IndividualSerializer(identity_detail).data
        except IndividualProfile.DoesNotExist:
            return None
    elif partner_profile.partner_type == "Company":
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=partner_profile.partner_id)
            return BusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None
    else:
        return None


def get_company_detail(obj):
    if obj.package_provider.partner_type == "Company":
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=obj.package_provider.partner_id)
            return ShortBusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None
    else:
        return None


def get_hotel_info_detail(obj):
    try:
        hotel = HuzHotelDetail.objects.filter(hotel_for_package=obj)
        return HuzHotelSerializer(hotel, many=True).data
    except HuzHotelDetail.DoesNotExist:
        return None


def get_ziyarah_detail(obj):
    try:
        ziyarah = HuzZiyarahDetail.objects.filter(ziyarah_for_package=obj)
        return HuzZiyarahSerializer(ziyarah, many=True).data
    except HuzZiyarahDetail.DoesNotExist:
        return None


def get_transport_detail(obj):
    try:
        transport = HuzTransportDetail.objects.filter(transport_for_package=obj)
        return HuzTransportSerializer(transport, many=True).data
    except HuzTransportDetail.DoesNotExist:
        return None


def get_airline_detail(obj):
    try:
        airline = HuzAirlineDetail.objects.filter(airline_for_package=obj)
        return HuzAirlineSerializer(airline, many=True).data
    except HuzAirlineDetail.DoesNotExist:
        return None


def get_rating_count(obj):
    rating_data = BookingRatingAndReview.objects.filter(rating_for_partner=obj.package_provider).aggregate(
        total_stars=Sum('partner_total_stars'),
        rating_count=Count('rating_id')
    )
    rating_count=0
    average_stars=0
    total_stars = rating_data['total_stars'] or 0
    if rating_data['rating_count'] > 0:
        rating_count = rating_data['rating_count']  # Number of ratings
        average_stars = round(total_stars / rating_count, 1) if rating_count else 0

    return {
        'total_stars': total_stars,
        'rating_count': rating_count,
        'average_stars': average_stars
    }


class PartnerProfileSerializer(serializers.ModelSerializer):
    # Get Partner detail about -> Individual or company
    partner_type_and_detail = serializers.SerializerMethodField()
    # Get Partner offered services
    partner_service_detail = serializers.SerializerMethodField()
    wallet_amount = serializers.SerializerMethodField()

    class Meta:
        model = PartnerProfile
        fields = (
            'partner_session_token', 'user_name', 'email', 'name', 'country_code', 'phone_number', 'partner_type',
            'is_phone_verified', 'is_email_verified', 'is_address_exist', 'firebase_token', 'web_firebase_token',
            'account_status', 'wallet_amount', 'created_time',  'user_photo', 'partner_service_detail',
            'partner_type_and_detail'
        )

    def validate_email(self, value):
        regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
        if not re.fullmatch(regex, value):
            raise serializers.ValidationError("You've entered an invalid email.")
        return value

    def validate_phone_number(self, value):
        regex = r'^(\+\d{1,3}[\s-]?)?\d{10}$'
        if not re.fullmatch(regex, value):
            raise serializers.ValidationError("You've entered an invalid Phone Number.")
        return value

    def validate_password(self, obj):
        if (len(obj) < 8 or
                not re.search(r'[A-Z]', obj) or
                not re.search(r'[a-z]', obj) or
                not re.search(r'\d', obj) or
                not re.search(r'[\W_]', obj)):
            raise serializers.ValidationError(
                "Password must be at least 8 characters long and include at least one uppercase letter, one lowercase letter, one digit, and one special character."
            )
        return obj

    def get_wallet_amount(self, obj):
        return Wallet.objects.values_list('wallet_amount', flat=True).get(wallet_session=obj)

    def get_partner_service_detail(self, obj):
        try:
            service = PartnerServices.objects.get(services_of_partner=obj)
            return PartnerServiceSerializer(service).data
        except PartnerServices.DoesNotExist:
            return {}

    def get_partner_type_and_detail(self, obj):
        return get_type_and_detail(obj)


class PartnerServiceSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerServices
        fields = [
            'is_hajj_service_offer', 'is_umrah_service_offer', 'is_ziyarah_service_offer',
            'is_transport_service_offer', 'is_visa_service_offer'
        ]


class ShortBusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessProfile
        fields = ['company_name', 'total_experience', 'company_bio', 'company_logo',  'contact_name', 'contact_number']


class IndividualSerializer(serializers.ModelSerializer):
    class Meta:
        model = IndividualProfile
        fields = [
            'contact_name', 'contact_number', 'driving_license_number',
            'front_side_photo', 'back_side_photo'
        ]


class BusinessSerializer(serializers.ModelSerializer):
    class Meta:
        model = BusinessProfile
        fields = [
            'company_name', 'contact_name', 'contact_number', 'company_website', 'total_experience',
            'company_bio', 'license_type', 'license_number', 'license_certificate', 'company_logo'
        ]


class PartnerMailingDetailSerializer(serializers.ModelSerializer):
    class Meta:
        model = PartnerMailingDetail
        fields = [
            'address_id', 'street_address', 'address_line2', 'city', 'state', 'country', 'postal_code', 'lat', 'long'
        ]


class HuzBasicShortSerializer(serializers.ModelSerializer):
    partner_session_token = serializers.CharField(source='package_provider.partner_session_token', read_only=True)
    hotel_info_detail = serializers.SerializerMethodField()
    company_detail = serializers.SerializerMethodField()
    rating_count = serializers.SerializerMethodField()

    class Meta:
        model = HuzBasicDetail
        fields = [
            'huz_token', 'package_type', 'package_name', 'package_base_cost', 'cost_for_child', 'cost_for_infants',
            'cost_for_sharing', 'cost_for_quad', 'cost_for_triple', 'cost_for_double', 'cost_for_single',
            'mecca_nights', 'madinah_nights',
            'start_date', 'end_date', 'is_visa_included', 'is_airport_reception_included',
            'is_tour_guide_included', 'is_insurance_included', 'is_breakfast_included', 'is_lunch_included',
            'is_dinner_included', 'is_package_open_for_other_date', 'package_validity', 'package_status', 'package_stage',
            'partner_session_token', 'hotel_info_detail', 'company_detail', 'rating_count'
        ]

    def get_hotel_info_detail(self, obj):
        return get_hotel_info_detail(obj)

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_rating_count(self, obj):
        return get_rating_count(obj)


class HuzBasicSerializer(serializers.ModelSerializer):
    partner_session_token = serializers.CharField(source='package_provider.partner_session_token', read_only=True)
    airline_detail = serializers.SerializerMethodField()
    transport_detail = serializers.SerializerMethodField()
    hotel_detail = serializers.SerializerMethodField()
    ziyarah_detail = serializers.SerializerMethodField()
    company_detail = serializers.SerializerMethodField()
    rating_count = serializers.SerializerMethodField()

    class Meta:
        model = HuzBasicDetail
        fields = [
            'huz_token', 'package_type', 'package_name', 'package_base_cost', 'cost_for_child', 'cost_for_infants',
            'cost_for_sharing', 'cost_for_quad', 'cost_for_triple', 'cost_for_double', 'cost_for_single',
            'mecca_nights', 'madinah_nights', 'start_date', 'end_date', 'description', 'is_visa_included',
            'is_airport_reception_included', 'is_tour_guide_included', 'is_insurance_included', 'is_breakfast_included',
            'is_lunch_included', 'is_dinner_included', 'is_package_open_for_other_date', 'package_validity',
            'package_status', 'package_stage', 'created_time', 'partner_session_token', 'airline_detail',
            'transport_detail', 'hotel_detail', 'ziyarah_detail', 'company_detail', 'package_provider', 'rating_count'
        ]

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_hotel_detail(self, obj):
        return get_hotel_info_detail(obj)

    def get_airline_detail(self, obj):
        return get_airline_detail(obj)

    def get_transport_detail(self, obj):
        return get_transport_detail(obj)

    def get_ziyarah_detail(self, obj):
        return get_ziyarah_detail(obj)

    def get_rating_count(self, obj):
        return get_rating_count(obj)


class HuzHotelSerializer(serializers.ModelSerializer):
    class Meta:
        model = HuzHotelDetail
        fields = [
            'hotel_id', 'hotel_city', 'hotel_name', 'hotel_rating', 'room_sharing_type', 'hotel_distance',
            'distance_type', 'is_shuttle_services_included', 'is_air_condition', 'is_television', 'is_wifi',
            'is_elevator', 'is_attach_bathroom', 'is_washroom_amenities', 'is_english_toilet',
            'is_indian_toilet', 'is_laundry'
        ]


class HuzAirlineSerializer(serializers.ModelSerializer):

    class Meta:
        model = HuzAirlineDetail
        fields = ['airline_id', 'airline_name', 'ticket_type', 'flight_from', 'flight_to', 'return_flight_from', 'return_flight_to', 'is_return_flight_included', 'airline_for_package']


class HuzTransportSerializer(serializers.ModelSerializer):

    class Meta:
        model = HuzTransportDetail
        fields = ['transport_id', 'transport_name', 'transport_type', 'routes']


class HuzZiyarahSerializer(serializers.ModelSerializer):

    class Meta:
        model = HuzZiyarahDetail
        fields = ['ziyarah_id', 'ziyarah_list']


class PartnerBankAccountSerializer(serializers.ModelSerializer):

    class Meta:
        model = PartnerBankAccount
        fields = ['account_id', 'account_title', 'account_number', 'bank_name', 'branch_code', 'created_time', 'bank_account_for_partner']


class PartnerWithdrawSerializer(serializers.ModelSerializer):
    account_title = serializers.CharField(source='withdraw_bank.account_title', read_only=True)
    account_number = serializers.CharField(source='withdraw_bank.account_number', read_only=True)
    bank_name = serializers.CharField(source='withdraw_bank.bank_name', read_only=True)

    class Meta:
        model = PartnerWithdraw
        fields = ['account_title', 'account_number', 'bank_name', 'withdraw_amount', 'request_time', 'withdraw_status', 'process_time', 'withdraw_for_partner', 'withdraw_bank']


class PartnerTransactionSerializer(serializers.ModelSerializer):

    class Meta:
        model = PartnerTransactionHistory
        fields = ['transaction_id', 'transaction_code', 'transaction_amount', 'transaction_type', 'transaction_time', 'transaction_description']