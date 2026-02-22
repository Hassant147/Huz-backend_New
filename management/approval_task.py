from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from rest_framework.permissions import IsAdminUser
from common.models import UserProfile
from common.serializers import UserProfileSerializer
from partners.models import PartnerProfile, HuzBasicDetail, Wallet, PartnerTransactionHistory
from partners.serializers import PartnerProfileSerializer, HuzBasicSerializer
from common.logs_file import logger
from common.utility import send_company_approval_email, send_payment_verification_email, preparation_email
from booking.models import Booking, PartnersBookingPayment, Payment, PassportValidity
from booking.serializers import DetailBookingSerializer, PartnersBookingPaymentSerializer
from django.utils import timezone


class ApprovedORRejectCompanyView(APIView):
    permission_classes = [IsAdminUser]
    ACCOUNT_STATUS_CHOICES = ['Active', 'Pending', 'Deactivate', 'Block']
    @swagger_auto_schema(
        operation_description="Update partner account approval status.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the sales director (optional)'),
                'account_status': openapi.Schema(type=openapi.TYPE_STRING, description='New account status of the partner'),
            },
            required=['partner_session_token', 'account_status'],
        ),
        responses={
            200: "Success: Company profile updated",
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or sales director not found.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract data from request
            partner_session_token = request.data.get('partner_session_token')
            session_token = request.data.get('session_token')
            account_status = request.data.get('account_status')

            # Check for required parameters
            if not partner_session_token or not account_status:
                return Response({"message": "Missing user or account status information."}, status=status.HTTP_400_BAD_REQUEST)

            if account_status not in self.ACCOUNT_STATUS_CHOICES:
                return Response({"message": f"Invalid account status. Must be one of {', '.join(self.ACCOUNT_STATUS_CHOICES)}."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve partner profile based on session token
            user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Optionally link sales director to partner profile
            if session_token:
                sales_agent = UserProfile.objects.filter(user_type="sales_director", session_token=session_token).first()
                if not sales_agent:
                    return Response({"message": "Sales Director not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)
                user.sales_agent_token = sales_agent

            if account_status == "Active":
                if user.account_status != "Active":
                    send_company_approval_email(user.email, user.name)
            # Update account status and save changes
            user.account_status = account_status
            user.save()
            return Response({"message": "Company Profile has been updated."}, status=status.HTTP_200_OK)

        except Exception as e:
            # add in Logs file
            logger.error("Error updating company status: %s", str(e))
            return Response({"message": "Failed to update user status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetAllPendingApprovalsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all pending approval profiles.",
        responses={
            200: openapi.Response('Success: List of pending profiles fetched', PartnerProfileSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: No pending profiles found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Fetch the pending profiles based on the status
            pending_profiles = PartnerProfile.objects.filter(account_status="Pending")

            if pending_profiles.exists():
                serializer = PartnerProfileSerializer(pending_profiles, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)

            return Response({"message": "No pending profiles found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Error in GetAllPendingApprovalsView: {str(e)}")
            return Response({"message": "Failed to get pending profiles. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetAllApprovedCompaniesView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all approved partners profiles.",
        responses={
            200: openapi.Response('Success: List of approved profiles fetched', PartnerProfileSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: No Approved profiles found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Fetch the pending profiles based on the status
            pending_profiles = PartnerProfile.objects.filter(account_status="Active")

            if pending_profiles.exists():
                serializer = PartnerProfileSerializer(pending_profiles, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)

            return Response({"message": "No approved profiles found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Error in GetAllApprovedCompaniesView: {str(e)}")
            return Response({"message": "Failed to get approved profiles. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetAllSaleDirectorsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all sale directors profiles.",
        responses={
            200: openapi.Response('Success: List of sale directors profiles fetched', UserProfileSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: No Approved profiles found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Fetch the all sales director profiles based on the status
            sales_profiles = UserProfile.objects.filter(account_status="Active", user_type="sales_director")

            if sales_profiles.exists():
                serializer = UserProfileSerializer(sales_profiles, many=True)
                return Response(serializer.data, status=status.HTTP_200_OK)

            return Response({"message": "No profiles found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Error in GetAllSaleDirectorsView: {str(e)}")
            return Response({"message": "Failed to get sale directors profiles. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ApproveBookingPaymentView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Confirm payment and update booking status",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='User session token'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='Booking number'),
                'payment_id': openapi.Schema(type=openapi.TYPE_STRING, description='Payment Id')
            },
            required=['session_token', 'booking_number', 'payment_id']
        ),
        responses={
            200: openapi.Response('Booking status updated successfully', DetailBookingSerializer(many=False)),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Booking detail or user detail not found.",
            409: "Conflict: Only bookings with 'Paid' status can be confirmed.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        try:
            # Extract required data from the request
            session_token = request.data.get("session_token")
            booking_number = request.data.get("booking_number")
            payment_id = request.data.get("payment_id")

            # Check for missing required fields
            if not session_token or not booking_number:
                return Response({"message": "Missing required data fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user profile based on session token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve booking detail based on user and booking number
            booking_detail = Booking.objects.filter(order_by=user, booking_number=booking_number).first()
            if not booking_detail:
                return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

            # Check booking status
            # if booking_detail.booking_status != "Paid":
            #     return Response({"message": "Only bookings with 'Paid' status can be confirmed."},
            #                     status=status.HTTP_409_CONFLICT)

            check_payment = Payment.objects.filter(payment_id=payment_id).first()
            if check_payment:
                check_payment.payment_status = "Approved"
                check_payment.save()
            else:
                return Response({"message": "Payment record not found."}, status=status.HTTP_404_NOT_FOUND)

            # Update booking status and mark payment as received
            booking_detail.booking_status = "Confirm"
            booking_detail.is_payment_received = True
            booking_detail.save()

            # Create PassportValidity records based on the number of adults
            for _ in range(booking_detail.adults):
                PassportValidity.objects.create(passport_for_booking_number=booking_detail)

            send_payment_verification_email(user.email, user.name, booking_number)
            preparation_email(user.email, user.name, booking_detail.package_token.package_type)

            # Serialize the updated booking detail and return response
            serialized_booking = DetailBookingSerializer(booking_detail)
            return Response(serialized_booking.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in ConfirmPaymentView: {str(e)}")
            return Response({"message": "Failed to update payment status. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class FetchPaidBookingView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all bookings with status 'Paid'",
        responses={
            200: openapi.Response('Successfully retrieved booking details', DetailBookingSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: Booking detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Retrieve all bookings with status "Paid"
            booking_details = Booking.objects.filter(booking_status="Paid")

            # Check if any bookings were found
            if booking_details.exists():
                # Serialize the booking details
                serialized_booking = DetailBookingSerializer(booking_details, many=True)
                return Response(serialized_booking.data, status=status.HTTP_200_OK)

            # Return response if no bookings were found
            return Response({"message": "Booking detail not found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in FetchPaidBookingView: {str(e)}")
            return Response({"message": "Failed to fetch booking details. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageFeaturedPackageView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Update an existing Huz Hajj or Umrah package.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the partner'),
                'huz_token': openapi.Schema(type=openapi.TYPE_STRING, description='Huz package token'),
                'is_featured': openapi.Schema(type=openapi.TYPE_BOOLEAN, description='True or false'),
            },
            required=['partner_session_token', 'huz_token', 'is_featured']
        ),
        responses={
            200: openapi.Response("Successful update", HuzBasicSerializer),
            400: "Bad Request: Missing or invalid input data.",
            401: "Unauthorized: Admin permissions required.",
            404: "Not Found: User or package not found.",
            409: "Conflict: Account status or type issue.",
            500: "Server Error: Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        partner_session_token = request.data.get('partner_session_token')
        huz_token = request.data.get('huz_token')
        is_featured = request.data.get('is_featured')
        if not partner_session_token or not huz_token or not is_featured:
            return Response({"message": "Missing user or package information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        # Check the account status and partner type
        if user.account_status != "Active":
            return Response({"message": "Account status does not allow you to perform this task."}, status=status.HTTP_409_CONFLICT)

        # Retrieve the package based on the huz token
        package = HuzBasicDetail.objects.filter(huz_token=huz_token).first()
        if not package:
            return Response({"message": "Package not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

        try:
            package.is_featured = is_featured
            package.save()
            serialized_package = HuzBasicSerializer(package)
            return Response(serialized_package.data, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"ManageFeaturedPackageView - Put: {str(e)}")
            return Response({"message": "Failed to update package detail. Internal server error."},  status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnerReceiveAblePaymentsView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Fetch all bookings payment which are not 'Paid' to partners",
        responses={
            200: openapi.Response('Successfully retrieved partner receive able details', PartnersBookingPaymentSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: payment detail not found.",
            500: "Server Error: Internal server error."
        }
    )
    def get(self, request):
        try:
            # Retrieve all bookings with status "Paid"
            receive_able_details = PartnersBookingPayment.objects.filter(payment_status="NotPaid")

            # Check if any bookings were found
            if receive_able_details.exists():
                # Serialize the booking details
                serialized_booking = PartnersBookingPaymentSerializer(receive_able_details, many=True)
                return Response(serialized_booking.data, status=status.HTTP_200_OK)

            # Return response if no bookings were found
            return Response({"message": "Payment detail not found."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Log the exception and return an error response
            logger.error(f"Error in FetchPaidBookingView: {str(e)}")
            return Response({"message": "Failed to fetch booking details. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManagePartnerReceiveAblePaymentView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_summary="Manage Partner Receivable Payment",
        operation_description="Updates the payment status for a partner based on the booking number and session token provided.",
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['partner_session_token', 'booking_number'],
            properties={
                'partner_session_token': openapi.Schema(type=openapi.TYPE_STRING, description='The session token of the partner'),
                'booking_number': openapi.Schema(type=openapi.TYPE_STRING, description='The booking number associated with the payment'),
            },
        ),
        responses={
            200: openapi.Response(description="Successfully updated partner payment details.",
                                  schema=PartnersBookingPaymentSerializer),
            400: "Missing user or booking information.",
            404: "User or booking not found with the provided details.",
            409: "Account status does not allow you to perform this task.",
            500: "Failed to update partner payment detail. Internal server error."
        }
    )
    def put(self, request, *args, **kwargs):
        partner_session_token = request.data.get('partner_session_token')
        booking_number = request.data.get('booking_number')

        # Validate if both partner_session_token and booking_number are provided
        if not partner_session_token or not booking_number:
            return Response({"message": "Missing user or booking information."}, status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the partner profile based on the session token
        user = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
        if not user:
            return Response({"message": "User not found with the provided details."}, status=status.HTTP_404_NOT_FOUND)

        # Ensure the partner's account is active
        if user.account_status != "Active":
            return Response({"message": "Account status does not allow you to perform this task."},
                            status=status.HTTP_409_CONFLICT)

        # Retrieve the booking details based on the booking number
        booking_detail = Booking.objects.filter(booking_number=booking_number).first()
        if not booking_detail:
            return Response({"message": "Booking not found with the provided details."},
                            status=status.HTTP_404_NOT_FOUND)

        # Ensure the booking status is either "Completed" or "Closed"
        if booking_detail.booking_status not in ["Completed", "Closed"]:
            return Response({"message": "Only completed or closed case payments can be processed."},
                            status=status.HTTP_400_BAD_REQUEST)

        # Retrieve the receivable payment details for the partner and booking
        receive_able = PartnersBookingPayment.objects.filter(payment_for_partner=user,
                                                             payment_for_booking=booking_detail).first()
        if not receive_able:
            return Response({"message": "Payment detail not found with the provided details."},
                            status=status.HTTP_404_NOT_FOUND)

        # Retrieve the partner's wallet details
        wallet_detail = Wallet.objects.filter(wallet_session=user).first()
        if not wallet_detail:
            return Response({"message": "Partner wallet detail not found."}, status=status.HTTP_404_NOT_FOUND)

        try:
            # Process the payment based on the current payment status
            if receive_able.payment_status == "NotPaid":
                # Update payment status to "FirstPayment" and process the full amount
                receive_able.payment_status = "FirstPayment"
                receive_able.processed_amount = receive_able.receivable_amount
                receive_able.save()

                # Update the wallet amount with the receivable amount
                wallet_detail.wallet_amount += receive_able.receivable_amount
                wallet_detail.save()

                # Log the transaction in the partner's transaction history
                PartnerTransactionHistory.objects.create(
                    transaction_amount=receive_able.receivable_amount,
                    transaction_type="Credit",
                    transaction_for_partner=user,
                    transaction_wallet_token=wallet_detail,
                    transaction_for_package=booking_detail.package_token,
                    transaction_description=f"You have credited {receive_able.receivable_amount} for booking number {booking_detail.booking_number}."
                )

            elif receive_able.payment_status == "FirstPayment":
                # Update payment status to "FinalPayment" and process the pending amount
                receive_able.payment_status = "FinalPayment"
                receive_able.processed_amount += receive_able.pending_amount
                receive_able.processed_date = timezone.now()
                receive_able.save()

                # Update the wallet amount with the pending amount
                wallet_detail.wallet_amount += receive_able.pending_amount
                wallet_detail.save()

                # Log the transaction in the partner's transaction history
                PartnerTransactionHistory.objects.create(
                    transaction_amount=receive_able.pending_amount,
                    transaction_type="Credit",
                    transaction_for_partner=user,
                    transaction_wallet_token=wallet_detail,
                    transaction_for_package=booking_detail.package_token,
                    transaction_description=f"You have credited {receive_able.pending_amount} for booking number {booking_detail.booking_number}."
                )

            # Serialize and return the updated payment details
            serialized_booking = PartnersBookingPaymentSerializer(receive_able)
            return Response(serialized_booking.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Log the error and return a 500 response
            logger.error(f"ManagePartnerReceiveAblePaymentView - Put: {str(e)}")
            return Response({"message": "Failed to update partner payment details. Internal server error."},
                            status=status.HTTP_500_INTERNAL_SERVER_ERROR)