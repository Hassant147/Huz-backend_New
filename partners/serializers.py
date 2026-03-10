from datetime import timedelta

from rest_framework import serializers
from django.db.models import Count, Sum
from django.utils import timezone
from booking.models import BookingRatingAndReview
import re
from .models import (PartnerProfile, Wallet, PartnerServices, IndividualProfile, BusinessProfile, PartnerMailingDetail,
                     HuzBasicDetail, HuzAirlineDetail, HuzTransportDetail, HuzHotelDetail, HuzHotelImage, HuzZiyarahDetail,
                     HuzPackageDateRange,
                     PartnerBankAccount, PartnerWithdraw, PartnerTransactionHistory)


def _get_prefetched_items(instance, relation_name):
    prefetched_cache = getattr(instance, '_prefetched_objects_cache', None) or {}
    if relation_name not in prefetched_cache:
        return None
    return list(prefetched_cache.get(relation_name) or [])


def _list_related_items(instance, relation_name):
    prefetched_items = _get_prefetched_items(instance, relation_name)
    if prefetched_items is not None:
        return prefetched_items

    relation = getattr(instance, relation_name, None)
    if relation is None:
        return []

    try:
        return list(relation.all())
    except Exception:
        return []


def _collect_hotel_images(instance):
    hotel_images = _list_related_items(instance, "hotel_images")

    if hotel_images:
        return hotel_images

    catalog_hotel = getattr(instance, "catalog_hotel", None)
    if not catalog_hotel:
        return []

    return _list_related_items(catalog_hotel, "hotel_images")


def _resolve_package_date_range_validity(range_item):
    package_validity = getattr(range_item, "package_validity", None)
    if package_validity:
        return package_validity

    start_date = getattr(range_item, "start_date", None)
    if not start_date:
        return None

    return start_date - timedelta(days=2)


def _to_local_date(value):
    if not value:
        return None

    if timezone.is_aware(value):
        value = timezone.localtime(value)

    return value.date() if hasattr(value, "date") else None


def _is_package_date_range_expired(range_item, reference_date=None):
    validity_date = _to_local_date(_resolve_package_date_range_validity(range_item))
    if validity_date is None:
        return False

    return (reference_date or timezone.localdate()) > validity_date


def get_type_and_detail(partner_profile):
    if partner_profile.partner_type == "Individual":
        prefetched_individuals = _get_prefetched_items(partner_profile, 'individual_profile_of_partner')
        if prefetched_individuals is not None:
            if prefetched_individuals:
                return IndividualSerializer(prefetched_individuals[0]).data
            return None
        try:
            identity_detail = IndividualProfile.objects.get(individual_profile_of_partner=partner_profile.partner_id)
            return IndividualSerializer(identity_detail).data
        except IndividualProfile.DoesNotExist:
            return None
    elif partner_profile.partner_type == "Company":
        prefetched_companies = _get_prefetched_items(partner_profile, 'company_of_partner')
        if prefetched_companies is not None:
            if prefetched_companies:
                return BusinessSerializer(prefetched_companies[0]).data
            return None
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=partner_profile.partner_id)
            return BusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None
    else:
        return None


def get_company_detail(obj):
    if obj.package_provider.partner_type == "Company":
        prefetched_companies = _get_prefetched_items(obj.package_provider, 'company_of_partner')
        if prefetched_companies is not None:
            if prefetched_companies:
                return ShortBusinessSerializer(prefetched_companies[0]).data
            return None
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=obj.package_provider.partner_id)
            return ShortBusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None
    else:
        return None


def get_hotel_info_detail(obj):
    prefetched_hotels = _get_prefetched_items(obj, "hotel_for_package")
    if prefetched_hotels is not None:
        return HuzHotelSerializer(prefetched_hotels, many=True).data

    try:
        hotel = HuzHotelDetail.objects.filter(hotel_for_package=obj).select_related(
            "catalog_hotel"
        ).prefetch_related("hotel_images", "catalog_hotel__hotel_images")
        return HuzHotelSerializer(hotel, many=True).data
    except HuzHotelDetail.DoesNotExist:
        return None


def get_ziyarah_detail(obj):
    prefetched_ziyarah = _get_prefetched_items(obj, "ziyarah_for_package")
    if prefetched_ziyarah is not None:
        return HuzZiyarahSerializer(prefetched_ziyarah, many=True).data

    try:
        ziyarah = HuzZiyarahDetail.objects.filter(ziyarah_for_package=obj)
        return HuzZiyarahSerializer(ziyarah, many=True).data
    except HuzZiyarahDetail.DoesNotExist:
        return None


def get_transport_detail(obj):
    prefetched_transport = _get_prefetched_items(obj, "transport_for_package")
    if prefetched_transport is not None:
        return HuzTransportSerializer(prefetched_transport, many=True).data

    try:
        transport = HuzTransportDetail.objects.filter(transport_for_package=obj)
        return HuzTransportSerializer(transport, many=True).data
    except HuzTransportDetail.DoesNotExist:
        return None


def get_airline_detail(obj):
    prefetched_airline = _get_prefetched_items(obj, "airline_for_package")
    if prefetched_airline is not None:
        return HuzAirlineSerializer(prefetched_airline, many=True).data

    try:
        airline = HuzAirlineDetail.objects.filter(airline_for_package=obj)
        return HuzAirlineSerializer(airline, many=True).data
    except HuzAirlineDetail.DoesNotExist:
        return None


def get_rating_count(obj):
    prefetched_ratings = _get_prefetched_items(obj, "rating_for_package")
    if prefetched_ratings is not None:
        total_stars = sum((rating.partner_total_stars or 0) for rating in prefetched_ratings)
        rating_count = len(prefetched_ratings)
        average_stars = round(total_stars / rating_count, 1) if rating_count else 0
        return {
            'total_stars': total_stars,
            'rating_count': rating_count,
            'average_stars': average_stars
        }

    annotated_rating_count = getattr(obj, "package_rating_total_count", None)
    annotated_total_stars = getattr(obj, "package_rating_total_stars", None)
    if annotated_rating_count is not None or annotated_total_stars is not None:
        total_stars = float(annotated_total_stars or 0)
        rating_count = int(annotated_rating_count or 0)
        average_stars = round(total_stars / rating_count, 1) if rating_count else 0
        return {
            'total_stars': total_stars,
            'rating_count': rating_count,
            'average_stars': average_stars
        }

    rating_data = BookingRatingAndReview.objects.filter(rating_for_package=obj).aggregate(
        total_stars=Sum('partner_total_stars'),
        rating_count=Count('rating_id')
    )
    rating_count = 0
    average_stars = 0
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
    mailing_detail = serializers.SerializerMethodField()
    wallet_amount = serializers.SerializerMethodField()

    class Meta:
        model = PartnerProfile
        fields = (
            'partner_session_token', 'user_name', 'email', 'name', 'country_code', 'phone_number', 'partner_type',
            'is_phone_verified', 'is_email_verified', 'is_address_exist', 'firebase_token', 'web_firebase_token',
            'account_status', 'wallet_amount', 'created_time',  'user_photo', 'partner_service_detail',
            'partner_type_and_detail', 'mailing_detail'
        )

    def to_representation(self, instance):
        data = super().to_representation(instance)
        if data.get('account_status') == "UnderReview":
            data['account_status'] = "Pending"
        return data

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
        prefetched_wallets = _get_prefetched_items(obj, 'wallet_session')
        if prefetched_wallets is not None:
            return prefetched_wallets[0].wallet_amount if prefetched_wallets else 0.0

        wallet_amount = Wallet.objects.filter(wallet_session=obj).values_list('wallet_amount', flat=True).first()
        return wallet_amount if wallet_amount is not None else 0.0

    def get_partner_service_detail(self, obj):
        prefetched_services = _get_prefetched_items(obj, 'services_of_partner')
        if prefetched_services is not None:
            return PartnerServiceSerializer(prefetched_services[0]).data if prefetched_services else {}

        try:
            service = PartnerServices.objects.get(services_of_partner=obj)
            return PartnerServiceSerializer(service).data
        except PartnerServices.DoesNotExist:
            return {}

    def get_partner_type_and_detail(self, obj):
        return get_type_and_detail(obj)

    def get_mailing_detail(self, obj):
        prefetched_mailing = _get_prefetched_items(obj, 'mailing_of_partner')
        if prefetched_mailing is not None:
            mailing_detail = prefetched_mailing[0] if prefetched_mailing else None
        else:
            mailing_detail = PartnerMailingDetail.objects.filter(mailing_of_partner=obj).first()
        if not mailing_detail:
            return {}
        return PartnerMailingDetailSerializer(mailing_detail).data


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
            'company_id', 'company_name', 'contact_name', 'contact_number', 'company_website', 'total_experience',
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
            'discount_if_child_with_bed', 'mecca_nights', 'madinah_nights', 'jeddah_nights', 'taif_nights', 'riyadah_nights',
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
            'discount_if_child_with_bed', 'mecca_nights', 'madinah_nights', 'jeddah_nights', 'taif_nights',
            'riyadah_nights', 'start_date', 'end_date', 'description', 'is_visa_included',
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


class HuzPackageDateRangeSerializer(serializers.ModelSerializer):
    is_expired = serializers.SerializerMethodField()

    class Meta:
        model = HuzPackageDateRange
        fields = [
            "range_id",
            "start_date",
            "end_date",
            "group_capacity",
            "package_validity",
            "is_expired",
        ]

    def get_is_expired(self, obj):
        return _is_package_date_range_expired(obj)


class HuzHotelSerializer(serializers.ModelSerializer):
    images = serializers.SerializerMethodField()
    primary_image = serializers.SerializerMethodField()

    def _serialized_images(self, instance):
        if not hasattr(self, "_image_cache"):
            self._image_cache = {}

        cache_key = str(instance.hotel_id)
        if cache_key not in self._image_cache:
            self._image_cache[cache_key] = HuzHotelImageSerializer(
                _collect_hotel_images(instance),
                many=True,
                context=self.context,
            ).data

        return self._image_cache[cache_key]

    def get_images(self, obj):
        return self._serialized_images(obj)

    def get_primary_image(self, obj):
        images = self._serialized_images(obj)
        if not images:
            return None
        return images[0].get("hotel_image")

    class Meta:
        model = HuzHotelDetail
        fields = [
            'hotel_id', 'hotel_city', 'hotel_name', 'hotel_rating', 'room_sharing_type', 'hotel_distance',
            'distance_type', 'is_shuttle_services_included', 'is_air_condition', 'is_television', 'is_wifi',
            'is_elevator', 'is_attach_bathroom', 'is_washroom_amenities', 'is_english_toilet',
            'is_indian_toilet', 'is_laundry', 'catalog_hotel', 'images', 'primary_image'
        ]
        read_only_fields = ('images', 'primary_image')


class HuzHotelImageSerializer(serializers.ModelSerializer):

    class Meta:
        model = HuzHotelImage
        fields = ['image_id', 'hotel_image', 'image_for_hotel', 'sort_order']
        extra_kwargs = {
            "image_for_hotel": {"required": False},
        }


class HuzAirlineSerializer(serializers.ModelSerializer):

    class Meta:
        model = HuzAirlineDetail
        fields = ['airline_id', 'airline_name', 'ticket_type', 'flight_from', 'flight_to', 'return_flight_from', 'return_flight_to', 'is_return_flight_included', 'airline_for_package']
        extra_kwargs = {
            "airline_for_package": {"required": False},
        }


class HuzTransportSerializer(serializers.ModelSerializer):

    class Meta:
        model = HuzTransportDetail
        fields = ['transport_id', 'transport_name', 'transport_type', 'routes']


class HuzZiyarahSerializer(serializers.ModelSerializer):

    class Meta:
        model = HuzZiyarahDetail
        fields = ['ziyarah_id', 'ziyarah_list']


def _get_sorted_package_date_ranges(obj):
    range_items = _list_related_items(obj, "package_date_ranges")
    if not range_items:
        range_items = list(
            HuzPackageDateRange.objects.filter(date_range_for_package=obj).order_by(
                "start_date", "end_date"
            )
        )

    return sorted(
        range_items,
        key=lambda item: (
            getattr(item, "start_date", None) or timezone.now(),
            getattr(item, "end_date", None) or timezone.now(),
        ),
    )


def _serialize_package_date_ranges(obj):
    range_items = _get_sorted_package_date_ranges(obj)
    if range_items:
        return HuzPackageDateRangeSerializer(range_items, many=True).data

    if not obj.start_date and not obj.end_date and not obj.package_validity:
        return []

    return [
        {
            "range_id": None,
            "start_date": obj.start_date,
            "end_date": obj.end_date,
            "group_capacity": None,
            "package_validity": obj.package_validity,
            "is_expired": _is_package_date_range_expired(obj),
        }
    ]


def _get_primary_package_date_range(obj):
    range_items = _get_sorted_package_date_ranges(obj)
    if not range_items:
        return None

    now = timezone.now()
    future_ranges = [
        item for item in range_items if getattr(item, "start_date", None) and item.start_date >= now
    ]
    active_future_ranges = [
        item for item in future_ranges if not _is_package_date_range_expired(item)
    ]
    return active_future_ranges[0] if active_future_ranges else future_ranges[0] if future_ranges else range_items[0]


class HuzAlignedPackageSerializer(serializers.ModelSerializer):
    huz_id = serializers.UUIDField(read_only=True)
    package_cost = serializers.FloatField(source="package_base_cost", read_only=True)
    partner_session_token = serializers.CharField(
        source="package_provider.partner_session_token",
        read_only=True,
    )

    airline_detail = serializers.SerializerMethodField()
    transport_detail = serializers.SerializerMethodField()
    hotel_detail = serializers.SerializerMethodField()
    ziyarah_detail = serializers.SerializerMethodField()

    package_date_range = serializers.SerializerMethodField()
    company_detail = serializers.SerializerMethodField()
    rating_count = serializers.SerializerMethodField()
    is_landed = serializers.SerializerMethodField()
    package_capacity = serializers.SerializerMethodField()

    class Meta:
        model = HuzBasicDetail
        fields = [
            "huz_id",
            "huz_token",
            "package_type",
            "package_name",
            "package_base_cost",
            "package_cost",
            "cost_for_child",
            "cost_for_infants",
            "cost_for_sharing",
            "cost_for_quad",
            "cost_for_triple",
            "cost_for_double",
            "cost_for_single",
            "discount_if_child_with_bed",
            "mecca_nights",
            "madinah_nights",
            "jeddah_nights",
            "taif_nights",
            "riyadah_nights",
            "description",
            "is_visa_included",
            "is_airport_reception_included",
            "is_tour_guide_included",
            "is_insurance_included",
            "is_breakfast_included",
            "is_lunch_included",
            "is_dinner_included",
            "is_package_open_for_other_date",
            "package_date_range",
            "package_capacity",
            "is_landed",
            "package_status",
            "package_stage",
            "created_time",
            "partner_session_token",
            "airline_detail",
            "transport_detail",
            "hotel_detail",
            "ziyarah_detail",
            "company_detail",
            "rating_count",
        ]

    def _get_airline_items(self, obj):
        items = _list_related_items(obj, "airline_for_package")
        if items:
            return items

        airline = HuzAirlineDetail.objects.filter(airline_for_package=obj).first()
        return [airline] if airline else []

    def _get_transport_items(self, obj):
        items = _list_related_items(obj, "transport_for_package")
        if items:
            return items

        transport = HuzTransportDetail.objects.filter(transport_for_package=obj).first()
        return [transport] if transport else []

    def _get_ziyarah_items(self, obj):
        items = _list_related_items(obj, "ziyarah_for_package")
        if items:
            return items

        ziyarah = HuzZiyarahDetail.objects.filter(ziyarah_for_package=obj).first()
        return [ziyarah] if ziyarah else []

    def _get_hotel_items(self, obj):
        items = _list_related_items(obj, "hotel_for_package")
        if items:
            return items

        return list(
            HuzHotelDetail.objects.filter(hotel_for_package=obj)
            .select_related("catalog_hotel")
            .prefetch_related("hotel_images", "catalog_hotel__hotel_images")
        )

    def _get_primary_capacity(self, obj):
        primary_range = _get_primary_package_date_range(obj)
        if not primary_range:
            return None

        capacity = getattr(primary_range, "group_capacity", None)
        return int(capacity) if capacity is not None else None

    def get_airline_detail(self, obj):
        items = self._get_airline_items(obj)
        if not items:
            return None
        return HuzAirlineSerializer(items[0], context=self.context).data

    def get_transport_detail(self, obj):
        items = self._get_transport_items(obj)
        if not items:
            return None
        return HuzTransportSerializer(items[0], context=self.context).data

    def get_ziyarah_detail(self, obj):
        items = self._get_ziyarah_items(obj)
        if not items:
            return None
        return HuzZiyarahSerializer(items[0], context=self.context).data

    def get_hotel_detail(self, obj):
        hotel_items = self._get_hotel_items(obj)
        if not hotel_items:
            return []
        return HuzHotelSerializer(hotel_items, many=True, context=self.context).data

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def get_rating_count(self, obj):
        context = self.context if isinstance(self.context, dict) else {}
        rating_cache = context.setdefault("package_rating_cache", {})

        package_id = str(obj.huz_id)
        if package_id in rating_cache:
            return rating_cache[package_id]

        result = get_rating_count(obj)
        rating_cache[package_id] = result
        return result

    def get_package_date_range(self, obj):
        return _serialize_package_date_ranges(obj)

    def get_is_landed(self, obj):
        return len(self._get_airline_items(obj)) == 0

    def get_package_capacity(self, obj):
        return self._get_primary_capacity(obj)


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
