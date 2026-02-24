from rest_framework import status
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from django.utils.dateparse import parse_datetime
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Prefetch
from partners.models import PartnerProfile, HuzBasicDetail, BusinessProfile, PartnerMailingDetail
from booking.models import BookingRatingAndReview, Booking, BookingComplaints
from common.models import UserProfile
from django.db.models import Count, Sum, F
from common.logs_file import logger


def _parse_date_filters(request):
    start_raw = request.query_params.get('start_date', None)
    end_raw = request.query_params.get('end_date', None)

    start_date = None
    end_date = None

    if start_raw:
        start_date = parse_datetime(start_raw)
        if start_date is None:
            return None, None, Response({"detail": "Invalid start_date format."}, status=status.HTTP_400_BAD_REQUEST)

    if end_raw:
        end_date = parse_datetime(end_raw)
        if end_date is None:
            return None, None, Response({"detail": "Invalid end_date format."}, status=status.HTTP_400_BAD_REQUEST)

    return start_date, end_date, None


def _build_partner_lookup(partner_ids):
    if not partner_ids:
        return {}

    partners = PartnerProfile.objects.filter(partner_id__in=partner_ids).only(
        'partner_id', 'user_name', 'name', 'email', 'phone_number'
    ).prefetch_related(
        Prefetch(
            'mailing_of_partner',
            queryset=PartnerMailingDetail.objects.only('mailing_of_partner_id', 'city', 'country'),
        ),
        Prefetch(
            'company_of_partner',
            queryset=BusinessProfile.objects.only('company_of_partner_id', 'company_name', 'company_logo'),
        ),
    )
    return {partner.partner_id: partner for partner in partners}


def _extract_partner_summary(partner):
    mailing_records = getattr(partner, '_prefetched_objects_cache', {}).get('mailing_of_partner') or []
    company_records = getattr(partner, '_prefetched_objects_cache', {}).get('company_of_partner') or []

    mailing = mailing_records[0] if mailing_records else None
    company = company_records[0] if company_records else None

    return {
        'country': mailing.country if mailing else "N/A",
        'city': mailing.city if mailing else "N/A",
        'company_name': company.company_name if company else "N/A",
        'company_logo': company.company_logo.url if company and company.company_logo else None,
    }


class PartnerStatusCountView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get count of partners by account status (Active, Pending, Deactivate, Block)",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Country of the partner", type=openapi.TYPE_STRING),
            openapi.Parameter('city', openapi.IN_QUERY, description="City of the partner", type=openapi.TYPE_STRING),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Start date for the account creation range", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="End date for the account creation range", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(description="Count of partners by account status", schema=openapi.Schema(
                type=openapi.TYPE_OBJECT,
                properties={
                    'Active': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Pending': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Deactivate': openapi.Schema(type=openapi.TYPE_INTEGER),
                    'Block': openapi.Schema(type=openapi.TYPE_INTEGER),
                }
            )),
            401: "Unauthorized: Admin permissions required.",
            400: openapi.Response(description="Bad request"),
            500: openapi.Response(description="Internal server error")
        }
    )
    def get(self, request):
        try:
            # Get the parameters from the request query params
            country = request.query_params.get('country')
            city = request.query_params.get('city')
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Start building the query
            queryset = PartnerProfile.objects.all()

            # Filter by country if provided (except when it is 'all')
            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            # Filter by city if provided (except when it is 'all')
            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            # Filter by date range if provided
            if start_date and end_date:
                queryset = queryset.filter(created_time__range=[start_date, end_date])
            elif start_date:
                queryset = queryset.filter(created_time__gte=start_date)
            elif end_date:
                queryset = queryset.filter(created_time__lte=end_date)

            # Group by account status and count the number of partners in each status
            status_counts = queryset.values('account_status').annotate(count=Count('account_status'))

            # Prepare the response dictionary
            result = {
                'Active': 0,
                'Pending': 0,
                'Deactivate': 0,
                'Block': 0
            }

            # Map the counts to the response dictionary
            for statuss in status_counts:
                raw_status = (statuss.get('account_status') or "").strip()
                normalized_status = "Pending" if raw_status.lower() in {"underreview", "pending"} else raw_status
                result[normalized_status] = result.get(normalized_status, 0) + statuss['count']

            # Return the response with HTTP 200 OK status
            return Response(result, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error (optional)
            logger.error(f"PartnerStatusCountView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TopPartnersRatingAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the top 5 partners based on their ratings and reviews",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter reviews from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter reviews until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="hajj, umrah", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Top 5 partners with their total stars and reviews count",
                examples={
                    "application/json": [
                        {
                            "partner_id": "uuid",
                            "name": "Partner Name",
                            "company_name": "Company name",
                            "total_stars": 4.5,
                            "num_reviews": 100,
                            "average_stars_per_review": 4.5,  # Added average stars per review
                            "email": "partner@example.com",
                            "phone_number": "+1234567890",
                            "country": "Country Name",
                            "city": "City Name",
                            "company_logo": "URL"
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the PartnerProfile model based on the country and city in PartnerMailingDetail
            queryset = PartnerProfile.objects.all()

            # Filter by country if provided (except when it is 'all')
            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            # Filter by city if provided (except when it is 'all')
            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            # Filter by package type if provided
            if package_type and package_type != 'all':
                # Here we are checking that the related HuzBasicDetail model's package_type matches the requested one
                queryset = queryset.filter(package_provider__package_type=package_type)

            partner_ids = list(queryset.values_list('partner_id', flat=True).distinct())
            if not partner_ids:
                return Response([], status=status.HTTP_200_OK)

            # Now, filter the related reviews based on the date range if provided
            partner_reviews = BookingRatingAndReview.objects.filter(rating_for_partner__in=partner_ids)

            if start_date:
                partner_reviews = partner_reviews.filter(rating_time__gte=start_date)
            if end_date:
                partner_reviews = partner_reviews.filter(rating_time__lte=end_date)

            # Aggregate the total stars and the number of reviews for each partner
            partner_reviews = list(
                partner_reviews.values('rating_for_partner')
                .annotate(total_stars=Sum('partner_total_stars'), num_reviews=Count('rating_id')) \
                .order_by('-total_stars', '-num_reviews')[:5]
            )

            if not partner_reviews:
                return Response([], status=status.HTTP_200_OK)

            # Get the partner IDs of the top 5 partners
            top_partner_ids = [review['rating_for_partner'] for review in partner_reviews]
            partner_lookup = _build_partner_lookup(top_partner_ids)

            # Prepare the response data
            response_data = []
            for review in partner_reviews:
                partner_id = review['rating_for_partner']
                partner = partner_lookup.get(partner_id)
                if not partner:
                    continue

                total_stars = review['total_stars'] or 0
                num_reviews = review['num_reviews'] or 0

                # Calculate average stars per review
                average_stars_per_review = total_stars / num_reviews if num_reviews > 0 else 0

                partner_meta = _extract_partner_summary(partner)

                # Append partner to the response data and mark this partner ID as added
                response_data.append({
                    'partner_id': partner.partner_id,
                    'name': partner.name,
                    'company_name': partner_meta['company_name'],
                    'total_stars': total_stars,
                    'num_reviews': num_reviews,
                    'average_stars_per_review': average_stars_per_review,  # Added average stars per review
                    'email': partner.email,
                    'phone_number': partner.phone_number,
                    'country': partner_meta['country'],
                    'city': partner_meta['city'],
                    'company_logo': partner_meta['company_logo']
                })

            # Sort by average_stars_per_review (ascending), and then by num_reviews (descending) if there's a tie
            sorted_response_data = sorted(response_data,
                                          key=lambda x: (-x['num_reviews'], -x['average_stars_per_review']))[:5]

            return Response(sorted_response_data, status=status.HTTP_200_OK)
        except Exception as e:
            # Log the error (optional)
            logger.error(f"TopPartnersRatingAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TopOperatorsWithTravelerAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the top 5 partners based on their number of travellers",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter reviews from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter reviews until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="hajj, umrah", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Top 5 partners with their number of travellers",
                examples={
                    "application/json": [
                        {
                            "partner_id": "some-uuid",
                            "user_name": "partner_1",
                            "company_name": "Company name",
                            "name": "Partner One",
                            "email": "partner1@example.com",
                            "phone_number": "123-456-7890",
                            "country": "USA",
                            "city": "New York",
                            "total_travelers": 150,
                            "company_logo": "URL",
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request, *args, **kwargs):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the PartnerProfile model based on the country and city in PartnerMailingDetail
            queryset = PartnerProfile.objects.all()

            # Filter by country if provided (except when it is 'all')
            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            # Filter by city if provided (except when it is 'all')
            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            # Filter by package_type if provided (except when it is 'all')
            if package_type and package_type != 'all':
                queryset = queryset.filter(package_provider__package_type=package_type)

            partner_ids = list(queryset.values_list('partner_id', flat=True).distinct())
            if not partner_ids:
                return Response([], status=status.HTTP_200_OK)

            valid_statuses = ['Objection', 'objection', 'Active', 'active', 'Completed', 'completed', 'Closed', 'closed', 'Report', 'report']
            # Filter bookings based on the start_date and end_date if provided
            if start_date and end_date:
                bookings_queryset = Booking.objects.filter(
                    start_date__gte=start_date,
                    end_date__lte=end_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            elif start_date:
                bookings_queryset = Booking.objects.filter(
                    start_date__gte=start_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            elif end_date:
                bookings_queryset = Booking.objects.filter(
                    end_date__lte=end_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            else:
                bookings_queryset = Booking.objects.filter(order_to__in=partner_ids, booking_status__in=valid_statuses)

            aggregated_travelers = list(
                bookings_queryset.values('order_to')
                .annotate(total_travelers=Sum(F('adults') + F('child') + F('infants')))
                .order_by('-total_travelers')[:5]
            )

            if not aggregated_travelers:
                return Response([], status=status.HTTP_200_OK)

            # Now retrieve the PartnerProfile for these partners and their address details
            lookup_partner_ids = [entry['order_to'] for entry in aggregated_travelers]
            partner_lookup = _build_partner_lookup(lookup_partner_ids)
            top_partners = []
            for entry in aggregated_travelers:
                partner = partner_lookup.get(entry['order_to'])
                if not partner:
                    continue

                partner_meta = _extract_partner_summary(partner)

                top_partners.append({
                    'partner_id': partner.partner_id,
                    'user_name': partner.user_name,
                    'company_name': partner_meta['company_name'],
                    'name': partner.name,
                    'email': partner.email,
                    'phone_number': partner.phone_number,
                    'country': partner_meta['country'],
                    'city': partner_meta['city'],
                    'total_travelers': entry['total_travelers'],
                    'company_logo': partner_meta['company_logo'],
                })

            return Response(top_partners, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"TopOperatorsWithTravllerAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TopOperatorsWithBookingAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the top 5 partners based on their number of bookings",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter reviews from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter reviews until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="hajj, umrah", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Top 5 partners with their number of bookings",
                examples={
                    "application/json": [
                        {
                            "partner_id": "some-uuid",
                            "user_name": "partner_1",
                            "company_name": "Company name",
                            "name": "Partner One",
                            "email": "partner1@example.com",
                            "phone_number": "123-456-7890",
                            "country": "USA",
                            "city": "New York",
                            "total_booking": 150,
                            "company_logo": "URL",
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request, *args, **kwargs):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the PartnerProfile model based on the country and city in PartnerMailingDetail
            queryset = PartnerProfile.objects.all()

            # Filter by country if provided (except when it is 'all')
            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            # Filter by city if provided (except when it is 'all')
            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            # Filter by package_type if provided (except when it is 'all')
            if package_type and package_type != 'all':
                queryset = queryset.filter(package_provider__package_type=package_type)

            partner_ids = list(queryset.values_list('partner_id', flat=True).distinct())
            if not partner_ids:
                return Response([], status=status.HTTP_200_OK)

            valid_statuses = ['Objection', 'objection', 'Active', 'active', 'Completed', 'completed', 'Closed', 'closed']
            # Filter bookings based on the start_date and end_date if provided
            if start_date and end_date:
                bookings_queryset = Booking.objects.filter(
                    start_date__gte=start_date,
                    end_date__lte=end_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            elif start_date:
                bookings_queryset = Booking.objects.filter(
                    start_date__gte=start_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            elif end_date:
                bookings_queryset = Booking.objects.filter(
                    end_date__lte=end_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            else:
                bookings_queryset = Booking.objects.filter(order_to__in=partner_ids, booking_status__in=valid_statuses)

            aggregated_bookings = list(
                bookings_queryset.values('order_to')
                .annotate(total_bookings=Count('booking_id'))
                .order_by('-total_bookings')[:5]
            )

            if not aggregated_bookings:
                return Response([], status=status.HTTP_200_OK)

            # Now retrieve the PartnerProfile for these partners and their address details
            lookup_partner_ids = [entry['order_to'] for entry in aggregated_bookings]
            partner_lookup = _build_partner_lookup(lookup_partner_ids)
            top_partners = []
            for entry in aggregated_bookings:
                partner = partner_lookup.get(entry['order_to'])
                if not partner:
                    continue

                partner_meta = _extract_partner_summary(partner)

                top_partners.append({
                    'partner_id': partner.partner_id,
                    'user_name': partner.user_name,
                    'company_name': partner_meta['company_name'],
                    'name': partner.name,
                    'email': partner.email,
                    'phone_number': partner.phone_number,
                    'country': partner_meta['country'],
                    'city': partner_meta['city'],
                    'total_bookings': entry['total_bookings'],
                    'company_logo': partner_meta['company_logo'],
                })

            return Response(top_partners, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"TopOperatorsWithBookingAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TopOperatorsWithBusinessAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the top 5 partners based on their total business",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter reviews from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter reviews until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="hajj, umrah", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Top 5 partners with their total business",
                examples={
                    "application/json": [
                        {
                            "partner_id": "some-uuid",
                            "user_name": "partner_1",
                            "company_name": "Company name",
                            "name": "Partner One",
                            "email": "partner1@example.com",
                            "phone_number": "123-456-7890",
                            "country": "USA",
                            "city": "New York",
                            "total_price": 150,
                            "company_logo": "URL",
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request, *args, **kwargs):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the PartnerProfile model based on the country and city in PartnerMailingDetail
            queryset = PartnerProfile.objects.all()

            # Filter by country if provided (except when it is 'all')
            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            # Filter by city if provided (except when it is 'all')
            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            # Filter by package_type if provided (except when it is 'all')
            if package_type and package_type != 'all':
                queryset = queryset.filter(package_provider__package_type=package_type)

            partner_ids = list(queryset.values_list('partner_id', flat=True).distinct())
            if not partner_ids:
                return Response([], status=status.HTTP_200_OK)

            valid_statuses = ['Pending', 'pending', 'Confirm', 'confirm', 'Objection', 'objection', 'Active', 'active', 'Completed', 'completed', 'Closed', 'closed']
            # Filter bookings based on the start_date and end_date if provided
            if start_date and end_date:
                bookings_queryset = Booking.objects.filter(
                    start_date__gte=start_date,
                    end_date__lte=end_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            elif start_date:
                bookings_queryset = Booking.objects.filter(
                    start_date__gte=start_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            elif end_date:
                bookings_queryset = Booking.objects.filter(
                    end_date__lte=end_date,
                    order_to__in=partner_ids,
                    booking_status__in=valid_statuses
                )
            else:
                bookings_queryset = Booking.objects.filter(order_to__in=partner_ids, booking_status__in=valid_statuses)

            aggregated_business = list(
                bookings_queryset.values('order_to')
                .annotate(total_price=Sum('total_price'))
                .order_by('-total_price')[:5]
            )

            if not aggregated_business:
                return Response([], status=status.HTTP_200_OK)

            # Now retrieve the PartnerProfile for these partners and their address details
            lookup_partner_ids = [entry['order_to'] for entry in aggregated_business]
            partner_lookup = _build_partner_lookup(lookup_partner_ids)
            top_partners = []
            for entry in aggregated_business:
                partner = partner_lookup.get(entry['order_to'])
                if not partner:
                    continue

                partner_meta = _extract_partner_summary(partner)

                top_partners.append({
                    'partner_id': partner.partner_id,
                    'user_name': partner.user_name,
                    'company_name': partner_meta['company_name'],
                    'name': partner.name,
                    'email': partner.email,
                    'phone_number': partner.phone_number,
                    'country': partner_meta['country'],
                    'city': partner_meta['city'],
                    'total_price': entry['total_price'],
                    'company_logo': partner_meta['company_logo'],
                })

            return Response(top_partners, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"TopOperatorsWithBusinessAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class TopPartnersComplaintsAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the top 5 partners based on their total number of complaints",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter complaints from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter complaints until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="hajj, umrah", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Top 5 partners with their total complaints count",
                examples={
                    "application/json": [
                        {
                            "partner_id": "uuid",
                            "name": "Partner Name",
                            "company_name": "Company name",
                            "total_complaints": 20,
                            "num_complaints": 20,
                            "email": "partner@example.com",
                            "phone_number": "+1234567890",
                            "country": "Country Name",
                            "city": "City Name",
                            "company_logo": "URL"
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the PartnerProfile model based on the country and city in PartnerMailingDetail
            queryset = PartnerProfile.objects.all()

            # Filter by country if provided (except when it is 'all')
            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            # Filter by city if provided (except when it is 'all')
            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            # Filter by package type if provided
            if package_type and package_type != 'all':
                queryset = queryset.filter(package_provider__package_type=package_type)

            partner_ids = list(queryset.values_list('partner_id', flat=True).distinct())
            if not partner_ids:
                return Response([], status=status.HTTP_200_OK)

            # Now, filter the related complaints based on the date range if provided
            partner_complaints = BookingComplaints.objects.filter(complaint_for_partner__in=partner_ids)

            if start_date:
                partner_complaints = partner_complaints.filter(complaint_time__gte=start_date)
            if end_date:
                partner_complaints = partner_complaints.filter(complaint_time__lte=end_date)

            # Aggregate the total number of complaints for each partner
            partner_complaints_count = list(
                partner_complaints.values('complaint_for_partner')
                .annotate(total_complaints=Count('complaint_id')) \
                .order_by('-total_complaints')[:5]
            )

            if not partner_complaints_count:
                return Response([], status=status.HTTP_200_OK)

            # Get the top 5 partners based on complaints
            top_partner_ids = [complaint['complaint_for_partner'] for complaint in partner_complaints_count]
            partner_lookup = _build_partner_lookup(top_partner_ids)

            # Prepare the response data
            response_data = []
            for complaint_entry in partner_complaints_count:
                partner = partner_lookup.get(complaint_entry['complaint_for_partner'])
                if not partner:
                    continue

                # Find the total complaints for the partner
                total_complaints = complaint_entry['total_complaints']
                partner_meta = _extract_partner_summary(partner)

                # Append partner to the response data and mark this partner ID as added
                response_data.append({
                    'partner_id': partner.partner_id,
                    'name': partner.name,
                    'company_name': partner_meta['company_name'],
                    'total_complaints': total_complaints,
                    'num_complaints': total_complaints,  # Total complaints count
                    'email': partner.email,
                    'phone_number': partner.phone_number,
                    'country': partner_meta['country'],
                    'city': partner_meta['city'],
                    'company_logo': partner_meta['company_logo']
                })

            # Sort by total_complaints (descending)
            sorted_response_data = sorted(response_data,
                                          key=lambda x: (-x['total_complaints']))[:5]

            return Response(sorted_response_data, status=status.HTTP_200_OK)
        except Exception as e:
            # Log the error (optional)
            logger.error(f"TopPartnersComplaintsAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class DistinctComplaintTitlesAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get distinct complaint titles and their counts",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter complaints from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter complaints until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="hajj, umrah", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="List of distinct complaint titles with their counts",
                examples={
                    "application/json": [
                        {
                            "complaint_title": "Complaint Title Example",
                            "complaint_count": 10
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request):
        try:
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter conditions
            complaints_queryset = BookingComplaints.objects.all()

            # Filter by country and city based on the PartnerProfile
            if country and country != 'all':
                complaints_queryset = complaints_queryset.filter(
                    complaint_for_partner__mailing_of_partner__country=country
                )

            if city and city != 'all':
                complaints_queryset = complaints_queryset.filter(
                    complaint_for_partner__mailing_of_partner__city=city
                )

            # Filter by package type
            if package_type and package_type != 'all':
                complaints_queryset = complaints_queryset.filter(
                    complaint_for_package__package_type=package_type
                )

            # Filter by complaint time range
            if start_date and end_date:
                complaints_queryset = complaints_queryset.filter(complaint_time__range=[start_date, end_date])
            elif start_date:
                complaints_queryset = complaints_queryset.filter(complaint_time__gte=start_date)
            elif end_date:
                complaints_queryset = complaints_queryset.filter(complaint_time__lte=end_date)

            # Count the number of complaints grouped by their title (complaint_title)
            complaint_count = complaints_queryset.values('complaint_title').annotate(
                count=Count('complaint_title')).order_by('complaint_title')

            # Return the result as JSON
            response_data = []
            for item in complaint_count:
                response_data.append({
                    'complaint_title': item['complaint_title'],
                    'count': item['count']
                })

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error (optional)
            logger.error(f"DistinctComplaintTitlesAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class ComplaintStatusCountAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get count of complaints grouped by their status (Open, InProgress, Close, Solved)",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter complaints from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter complaints until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="hajj, umrah", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Count of complaints grouped by status (Open, InProgress, Close, Solved)",
                examples={
                    "application/json": [
                        {
                            "status": "Open",
                            "count": 10
                        },
                        {
                            "status": "InProgress",
                            "count": 5
                        },
                        {
                            "status": "Close",
                            "count": 7
                        },
                        {
                            "status": "Solved",
                            "count": 8
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request):
        try:
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter conditions
            complaints_queryset = BookingComplaints.objects.all()

            # Filter by country and city based on the PartnerProfile
            if country and country != 'all':
                complaints_queryset = complaints_queryset.filter(
                    complaint_for_partner__mailing_of_partner__country=country
                )

            if city and city != 'all':
                complaints_queryset = complaints_queryset.filter(
                    complaint_for_partner__mailing_of_partner__city=city
                )

            # Filter by package type
            if package_type and package_type != 'all':
                complaints_queryset = complaints_queryset.filter(
                    complaint_for_package__package_type=package_type
                )

            # Filter by complaint time range
            if start_date and end_date:
                complaints_queryset = complaints_queryset.filter(complaint_time__range=[start_date, end_date])
            elif start_date:
                complaints_queryset = complaints_queryset.filter(complaint_time__gte=start_date)
            elif end_date:
                complaints_queryset = complaints_queryset.filter(complaint_time__lte=end_date)

            # Count the number of complaints grouped by their status (complaint_status)
            complaint_status_count = complaints_queryset.values('complaint_status').annotate(
                count=Count('complaint_status')).order_by('complaint_status')

            all_statuses = ["Open", "InProgress", "Close", "Solved"]
            response_data = []

            status_count_dict = {statuses: 0 for statuses in all_statuses}
            status_aliases = {
                "open": "Open",
                "inprogress": "InProgress",
                "in_progress": "InProgress",
                "close": "Close",
                "closed": "Close",
                "solved": "Solved",
            }

            # Normalize case to ensure no mismatches due to case sensitivity.
            for item in complaint_status_count:
                raw_status = (item.get('complaint_status') or '').strip().lower()
                normalized_key = raw_status.replace(' ', '_')
                mapped_status = status_aliases.get(normalized_key) or status_aliases.get(raw_status)
                if mapped_status in status_count_dict:
                    status_count_dict[mapped_status] += item['count']

            for statuses, count in status_count_dict.items():
                response_data.append({
                    'status': statuses,
                    'count': count
                })

            return Response(response_data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error (optional)
            logger.error(f"ComplaintStatusCountAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BookingWithEachAirlineAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the number of bookings with each airline based on filters",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter bookings from this start date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter bookings until this end date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Filter bookings by package type (e.g., hajj, umrah)", type=openapi.TYPE_STRING, default='hajj'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="List of airlines and their booking counts",
                examples={
                    "application/json": [
                        {
                            "airline_name": "Airline A",
                            "booking_count": 25
                        },
                        {
                            "airline_name": "Airline B",
                            "booking_count": 15
                        }
                    ]
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request):
        try:
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            start_date = request.query_params.get('start_date', None)
            end_date = request.query_params.get('end_date', None)
            package_type = request.query_params.get('package_type', None)

            # Validate input dates
            if start_date:
                start_date = parse_datetime(start_date)
                if not start_date:
                    return Response({"detail": "Invalid start_date format."}, status=status.HTTP_400_BAD_REQUEST)
            if end_date:
                end_date = parse_datetime(end_date)
                if not end_date:
                    return Response({"detail": "Invalid end_date format."}, status=status.HTTP_400_BAD_REQUEST)

            # Initialize the queryset for PartnerProfile
            queryset = PartnerProfile.objects.all()

            # Filter by country if provided (except when it is 'all')
            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            # Filter by city if provided (except when it is 'all')
            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            # Filter by package_type if provided (except when it is 'all')
            if package_type and package_type != 'all':
                queryset = queryset.filter(package_provider__package_type=package_type)

            partner_ids = queryset.values_list('package_provider__huz_id', flat=True)

            # Define valid booking statuses
            valid_statuses = ['Confirm', 'confirm', 'Objection', 'objection', 'Active', 'active',
                              'Completed', 'completed', 'Closed', 'closed']

            # Filter bookings based on the start_date and end_date if provided
            bookings_queryset = Booking.objects.filter(package_token__in=partner_ids, booking_status__in=valid_statuses)
            if start_date and end_date:
                bookings_queryset = bookings_queryset.filter(
                    start_date__gte=start_date,
                    end_date__lte=end_date
                )
            elif start_date:
                bookings_queryset = bookings_queryset.filter(start_date__gte=start_date)
            elif end_date:
                bookings_queryset = bookings_queryset.filter(end_date__lte=end_date)

            # Annotate the bookings with the count of bookings for each airline
            bookings_with_airlines = bookings_queryset.values('package_token__airline_for_package__airline_name') \
                .annotate(
                total_adults=Sum('adults'),
                total_children=Sum('child'),
                total_infants=Sum('infants'),
                total_travelers=Sum(F('adults') + F('child') + F('infants'))  # Total travelers as the sum of all three
            ) \
                .order_by('-total_travelers')

            # Prepare the response data
            report_data = [
                {
                    'airline_name': record['package_token__airline_for_package__airline_name'],
                    'total_adults': record['total_adults'],
                    'total_children': record['total_children'],
                    'total_infants': record['total_infants'],
                    'total_travelers': record['total_travelers']
                }
                for record in bookings_with_airlines
            ]

            return Response({'report': report_data}, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the exception with detailed information
            logger.error(f"Error generating airline booking report: {str(e)}", exc_info=True)
            return Response({"detail": "Internal server error. Please try again later."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class BookingStatusCountAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the count of bookings for each status based on the given parameters",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter bookings from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter bookings until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Filter by package type (e.g. hajj, umrah)", type=openapi.TYPE_STRING, default='all'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Booking status counts",
                examples={
                    "application/json": {
                        "initialize": 5,
                        "Passport_Validation": 2,
                        "paid": 8,
                        "confirm": 12,
                        "pending": 3,
                        "active": 4,
                        "completed": 7,
                        "closed": 2,
                        "objection": 1,
                    }
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request, *args, **kwargs):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the PartnerProfile model based on the country and city
            queryset = PartnerProfile.objects.all()

            if country and country != 'all':
                queryset = queryset.filter(mailing_of_partner__country=country)

            if city and city != 'all':
                queryset = queryset.filter(mailing_of_partner__city=city)

            if package_type and package_type != 'all':
                queryset = queryset.filter(package_provider__package_type=package_type)

            # Get partner_ids after applying filters
            partner_ids = queryset.values_list('partner_id', flat=True)

            # Define valid booking statuses
            valid_statuses = [
                'Initialize', 'Passport_Validation', 'Paid', 'Confirm', 'Pending', 'Active',
                'Completed', 'Closed', 'Objection', 'Report', 'Rejected'
            ]

            # Filter bookings based on the query parameters
            bookings_queryset = Booking.objects.filter(order_to__in=partner_ids, booking_status__in=valid_statuses)

            # Apply additional date filters if provided
            if start_date and end_date:
                bookings_queryset = bookings_queryset.filter(
                    start_date__gte=start_date,
                    end_date__lte=end_date
                )
            elif start_date:
                bookings_queryset = bookings_queryset.filter(start_date__gte=start_date)
            elif end_date:
                bookings_queryset = bookings_queryset.filter(end_date__lte=end_date)

            # Count the number of bookings for each status
            status_counts = bookings_queryset.values('booking_status') \
                                              .annotate(count=Count('booking_status')) \
                                              .order_by('booking_status')

            # Prepare the response data
            status_count_dict = {statues: 0 for statues in valid_statuses}
            for statues in status_counts:
                status_count_dict[statues['booking_status']] = statues['count']

            return Response(status_count_dict, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"BookingStatusCountAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class PackageStatusCountAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the count of packages for each status based on the given parameters",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter packages from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter packages until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Filter by package type (e.g. hajj, umrah)", type=openapi.TYPE_STRING, default='all'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Package status counts",
                examples={
                    "application/json": {
                        "initialize": 5,
                        "completed": 8,
                        "active": 12,
                        "deactivated": 3,
                        "block": 4,
                        "pending": 7
                    }
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request, *args, **kwargs):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the PartnerProfile model based on the country and city
            partner_queryset = PartnerProfile.objects.all()

            if country and country != 'all':
                partner_queryset = partner_queryset.filter(mailing_of_partner__country=country)

            if city and city != 'all':
                partner_queryset = partner_queryset.filter(mailing_of_partner__city=city)

            if package_type and package_type != 'all':
                partner_queryset = partner_queryset.filter(package_provider__package_type=package_type)

            # Get partner_ids after applying filters
            partner_ids = partner_queryset.values_list('partner_id', flat=True)

            # Define valid package statuses
            valid_statuses = ['Initialize', 'Completed', 'Active', 'Deactivated', 'Block', 'Pending']

            # Define valid package types
            valid_package_types = ['Hajj', 'Umrah', 'Ziyarah']

            # Filter HuzBasicDetail based on the partner_ids and the valid statuses
            huz_queryset = HuzBasicDetail.objects.filter(package_provider__partner_id__in=partner_ids, package_status__in=valid_statuses)

            # Apply package type filter if provided
            if package_type and package_type != 'all' and package_type in valid_package_types:
                huz_queryset = huz_queryset.filter(package_type=package_type)

            # Apply date filters if provided
            if start_date and end_date:
                huz_queryset = huz_queryset.filter(
                    start_date__gte=start_date,
                    end_date__lte=end_date
                )
            elif start_date:
                huz_queryset = huz_queryset.filter(start_date__gte=start_date)
            elif end_date:
                huz_queryset = huz_queryset.filter(end_date__lte=end_date)

            # Count the number of packages for each status, grouped by package type
            status_counts = huz_queryset.values('package_type', 'package_status') \
                                         .annotate(count=Count('package_status')) \
                                         .order_by('package_type', 'package_status')

            # Prepare the response data
            status_count_dict = {package_type: {statuses: 0 for statuses in valid_statuses} for package_type in valid_package_types}

            # Populate the count dictionary with actual counts from the queryset
            for statuses in status_counts:
                package_type = statuses['package_type']
                package_status = statuses['package_status']
                status_count_dict[package_type][package_status] = statuses['count']

            return Response(status_count_dict, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"PackageStatusCountAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class UserRegistrationCountAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the count of users who registered based on the given parameters",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter users from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter users until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Filter by package type (e.g. hajj, umrah, ziyarah)", type=openapi.TYPE_STRING, default='all'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Count of users based on the filters",
                examples={
                    "application/json": {
                        "register_users": 25
                    }
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request, *args, **kwargs):
        try:
            # Get query parameters
            country = request.query_params.get('country', None)
            city = request.query_params.get('city', None)
            package_type = request.query_params.get('package_type', None)
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Filter the UserProfile model based on the country, city, and date range
            user_queryset = UserProfile.objects.all()

            if country and country != 'all':
                # Filter based on country via MailingDetail's country
                user_queryset = user_queryset.filter(mailing_session__country=country)

            if city and city != 'all':
                # Filter based on city via MailingDetail's city
                user_queryset = user_queryset.filter(mailing_session__city=city)

            # Apply date filters if provided
            if start_date and end_date:
                user_queryset = user_queryset.filter(created_time__gte=start_date, created_time__lte=end_date)
            elif start_date:
                user_queryset = user_queryset.filter(created_time__gte=start_date)
            elif end_date:
                user_queryset = user_queryset.filter(created_time__lte=end_date)

            # UserProfile does not contain package type; rejecting this filter avoids misleading zero counts.
            if package_type and package_type != 'all':
                return Response(
                    {"detail": "package_type filter is not supported for register-users-count."},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Count the new users based on the filtered queryset
            new_user_count = user_queryset.count()

            return Response({"register_users": new_user_count}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error(f"NewUserRegistrationCountAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BookingTypeStatusCountWithPriceAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the count of bookings and their prices for each status based on the given parameters, grouped by package type.",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter bookings from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter bookings until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Filter by package type (e.g. hajj, umrah, ziyarah)", type=openapi.TYPE_STRING, default='all'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Booking status counts with price grouped by package type",
                examples={
                    "application/json": {
                        "Hajj": {
                            "Initialize": 0,
                            "Initialize_price": 0,
                            "Passport_Validation": 0,
                            "Passport_Validation_price": 0,
                            "Paid": 0,
                            "Paid_price": 0,
                            "Confirm": 0,
                            "Confirm_price": 0,
                            "Pending": 0,
                            "Pending_price": 0,
                            "Active": 0,
                            "Active_price": 0,
                            "Completed": 0,
                            "Completed_price": 0,
                            "Closed": 0,
                            "Closed_price": 0,
                            "Objection": 0,
                            "Objection_price": 0,
                            "Report": 0,
                            "Report_price": 0,
                            "Rejected": 0,
                            "Rejected_price": 0,
                        },
                        "Umrah": {
                            "Initialize": 0,
                            "Initialize_price": 0,
                            "Passport_Validation": 0,
                            "Passport_Validation_price": 0,
                            "Paid": 0,
                            "Paid_price": 0,
                            "Confirm": 0,
                            "Confirm_price": 0,
                            "Pending": 0,
                            "Pending_price": 0,
                            "Active": 0,
                            "Active_price": 0,
                            "Completed": 0,
                            "Completed_price": 0,
                            "Closed": 0,
                            "Closed_price": 0,
                            "Objection": 0,
                            "Objection_price": 0,
                            "Report": 0,
                            "Report_price": 0,
                            "Rejected": 0,
                            "Rejected_price": 0,
                        },
                        "Ziyarah": {
                            "Initialize": 0,
                            "Initialize_price": 0,
                            "Passport_Validation": 0,
                            "Passport_Validation_price": 0,
                            "Paid": 0,
                            "Paid_price": 0,
                            "Confirm": 0,
                            "Confirm_price": 0,
                            "Pending": 0,
                            "Pending_price": 0,
                            "Active": 0,
                            "Active_price": 0,
                            "Completed": 0,
                            "Completed_price": 0,
                            "Closed": 0,
                            "Closed_price": 0,
                            "Objection": 0,
                            "Objection_price": 0,
                            "Report": 0,
                            "Report_price": 0,
                            "Rejected": 0,
                            "Rejected_price": 0,
                        }
                    }
                }
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error")
        },
    )
    def get(self, request):
        try:
            country = request.query_params.get('country', 'all')
            city = request.query_params.get('city', 'all')
            package_type = request.query_params.get('package_type', 'all')
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Start with all bookings
            bookings = Booking.objects.all()

            # Apply country filter
            if country != 'all':
                bookings = bookings.filter(
                    package_token__package_provider__mailing_of_partner__country=country
                )

            # Apply city filter
            if city != 'all':
                bookings = bookings.filter(
                    package_token__package_provider__mailing_of_partner__city=city
                )

            # Apply date filters if provided
            if start_date and end_date:
                bookings = bookings.filter(start_date__gte=start_date, end_date__lte=end_date)
            elif start_date:
                bookings = bookings.filter(start_date__gte=start_date)
            elif end_date:
                bookings = bookings.filter(end_date__lte=end_date)

            # Apply package_type filter
            if package_type != 'all':
                bookings = bookings.filter(package_token__package_type=package_type)

            # Group by package_type and booking_status, annotate count and sum
            grouped_data = bookings.values(
                'package_token__package_type', 'booking_status'
            ).annotate(
                count=Count('booking_id'),
                total_price=Sum('total_price')
            )

            # Initialize result structure with all package types and statuses
            package_types = ['Hajj', 'Umrah', 'Ziyarah']
            booking_statuses = [choice[0] for choice in Booking.BOOKING_TYPE]

            result = {pt: {} for pt in package_types}
            for pt in package_types:
                for statuses in booking_statuses:
                    result[pt][statuses] = 0
                    result[pt][f"{statuses}_price"] = 0.0

            # Populate the result with grouped data
            for entry in grouped_data:
                pt = entry['package_token__package_type']
                statuses = entry['booking_status']
                count = entry['count']
                total_price = entry['total_price'] or 0.0

                if pt in result:
                    result[pt][statuses] = count
                    result[pt][f"{statuses}_price"] = total_price

            return Response(result)
        except Exception as e:
            logger.error(f"BookingTypeStatusCountWithPriceAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class BookingStatsByPackageAPIView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Get the total bookings, sum of total prices, and sum of number of adults, children, and infants for each package type (Hajj, Umrah, Ziyarah), with filters based on country, city, and date range.",
        manual_parameters=[
            openapi.Parameter('country', openapi.IN_QUERY, description="Filter by country", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('city', openapi.IN_QUERY, description="Filter by city", type=openapi.TYPE_STRING, default='all'),
            openapi.Parameter('start_date', openapi.IN_QUERY, description="Filter bookings from this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('end_date', openapi.IN_QUERY, description="Filter bookings until this date", type=openapi.TYPE_STRING, format=openapi.FORMAT_DATETIME),
            openapi.Parameter('package_type', openapi.IN_QUERY, description="Filter by package type (e.g. hajj, umrah, ziyarah)", type=openapi.TYPE_STRING, default='all'),
        ],
        tags=["Reports"],
        responses={
            200: openapi.Response(
                description="Total bookings stats for Hajj, Umrah, and Ziyarah with filtering",
                examples={
                    "application/json": {
                        "Hajj": {
                            "total_bookings": 0,
                            "total_price": 0.0,
                            "total_adults": 0,
                            "total_children": 0,
                            "total_infants": 0,
                        },
                        "Umrah": {
                            "total_bookings": 0,
                            "total_price": 0.0,
                            "total_adults": 0,
                            "total_children": 0,
                            "total_infants": 0,
                        },
                        "Ziyarah": {
                            "total_bookings": 0,
                            "total_price": 0.0,
                            "total_adults": 0,
                            "total_children": 0,
                            "total_infants": 0,
                        },
                    }
                },
            ),
            400: openapi.Response(description="Bad request, invalid parameters"),
            401: "Unauthorized: Admin permissions required.",
            500: openapi.Response(description="Internal server error"),
        },
    )
    def get(self, request):
        try:
            country = request.query_params.get('country', 'all')
            city = request.query_params.get('city', 'all')
            package_type = request.query_params.get('package_type', 'all')
            start_date, end_date, error_response = _parse_date_filters(request)
            if error_response:
                return error_response

            # Start with all bookings
            bookings = Booking.objects.all()

            # Apply country filter
            if country != 'all':
                bookings = bookings.filter(
                    package_token__package_provider__mailing_of_partner__country=country
                )

            # Apply city filter
            if city != 'all':
                bookings = bookings.filter(
                    package_token__package_provider__mailing_of_partner__city=city
                )

            # Apply date filters if provided
            if start_date and end_date:
                bookings = bookings.filter(start_date__gte=start_date, end_date__lte=end_date)
            elif start_date:
                bookings = bookings.filter(start_date__gte=start_date)
            elif end_date:
                bookings = bookings.filter(end_date__lte=end_date)

            # Apply package_type filter
            if package_type != 'all':
                bookings = bookings.filter(package_token__package_type=package_type)

            # Group by package_type and annotate counts and sums
            grouped_data = bookings.values('package_token__package_type').annotate(
                total_bookings=Count('booking_id'),
                total_price=Sum('total_price'),
                total_adults=Sum('adults'),
                total_children=Sum('child'),
                total_infants=Sum('infants')
            )

            # Initialize result structure for all package types
            package_types = ['Hajj', 'Umrah', 'Ziyarah']
            result = {pt: {
                "total_bookings": 0,
                "total_price": 0.0,
                "total_adults": 0,
                "total_children": 0,
                "total_infants": 0
            } for pt in package_types}

            # Populate the result with grouped data
            for entry in grouped_data:
                pt = entry['package_token__package_type']
                total_bookings = entry['total_bookings']
                total_price = entry['total_price'] or 0.0
                total_adults = entry['total_adults'] or 0
                total_children = entry['total_children'] or 0
                total_infants = entry['total_infants'] or 0

                if pt in result:
                    result[pt]["total_bookings"] = total_bookings
                    result[pt]["total_price"] = total_price
                    result[pt]["total_adults"] = total_adults
                    result[pt]["total_children"] = total_children
                    result[pt]["total_infants"] = total_infants

            return Response(result)
        except Exception as e:
            logger.error(f"BookingStatsByPackageAPIView - Get: {str(e)}")
            return Response(
                {"error": f"An unexpected error occurred: {str(e)}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
