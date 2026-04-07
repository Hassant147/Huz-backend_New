from rest_framework.views import APIView
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.exceptions import AuthenticationFailed
from rest_framework import status
from .models import PartnerProfile, PartnerWithdraw, PartnerBankAccount, Wallet, PartnerTransactionHistory
from .serializers import PartnerWithdrawSerializer, PartnerBankAccountSerializer, PartnerTransactionSerializer
from common.authentication import (
    SessionTokenHeaderAuthentication,
    get_authenticated_partner_profile,
)
from common.auth_utils import require_partner_profile
from common.utility import validate_required_fields
from common.logs_file import logger
from common.pagination import CustomPagination
from django.db import transaction
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Count, Q, Sum
from django.utils.dateparse import parse_date


WALLET_DEFAULT_PAGE_SIZE = 25
WALLET_MAX_PAGE_SIZE = 100
TRANSACTION_ALLOWED_SORTS = {
    "transaction_time": "transaction_time",
    "-transaction_time": "-transaction_time",
    "transaction_amount": "transaction_amount",
    "-transaction_amount": "-transaction_amount",
}
WITHDRAW_ALLOWED_SORTS = {
    "request_time": "request_time",
    "-request_time": "-request_time",
    "process_time": "process_time",
    "-process_time": "-process_time",
    "withdraw_amount": "withdraw_amount",
    "-withdraw_amount": "-withdraw_amount",
}


class WalletPagination(CustomPagination):
    page_size = WALLET_DEFAULT_PAGE_SIZE
    max_page_size = WALLET_MAX_PAGE_SIZE


def _parse_iso_date_param(raw_value):
    if raw_value in (None, ""):
        return None

    parsed = parse_date(str(raw_value))
    return parsed


def _normalize_sort_param(raw_value, allowed_sorts, default_sort):
    if raw_value in (None, ""):
        return default_sort

    normalized = str(raw_value).strip()
    return allowed_sorts.get(normalized, default_sort)


def _normalize_transaction_type(raw_value):
    if raw_value in (None, ""):
        return None

    normalized = str(raw_value).strip().lower()
    if normalized in {"all", "*"}:
        return None
    if normalized == "credit":
        return "Credit"
    if normalized == "debit":
        return "Debit"
    return "__invalid__"


def _normalize_withdraw_status(raw_value):
    if raw_value in (None, ""):
        return None

    normalized = str(raw_value).strip().lower()
    if normalized in {"all", "*"}:
        return None

    aliases = {
        "pending": "Pending",
        "processed": "Processed",
        "completed": "Completed",
        "rejected": "Rejected",
    }
    return aliases.get(normalized, "__invalid__")


class PartnerHeaderAuthenticationAPIView(APIView):
    authentication_classes = [SessionTokenHeaderAuthentication]

    @staticmethod
    def get_partner(request):
        return require_partner_profile(request)

    @staticmethod
    def get_request_data_without_partner_token(request):
        try:
            data = request.data.copy()
        except Exception:
            data = {}

        if hasattr(data, "pop"):
            data.pop("partner_session_token", None)
        return data

    @staticmethod
    def resolve_read_partner(request):
        partner = get_authenticated_partner_profile(request)
        if partner is not None:
            return partner, None

        return None, Response(
            {"message": "Authenticated partner profile is required."},
            status=status.HTTP_401_UNAUTHORIZED,
        )


class ManagePartnerBankAccountView(PartnerHeaderAuthenticationAPIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description=(
            "Retrieve bank accounts for the authenticated partner. "
            "Admin-session aliases may still supply `partner_session_token` through "
            "explicit compatibility helpers, but canonical operator requests use "
            "the Authorization header."
        ),
        manual_parameters=[
            openapi.Parameter(
                'partner_session_token',
                openapi.IN_QUERY,
                description='Optional for documented legacy/admin compatibility only.',
                type=openapi.TYPE_STRING,
                required=False,
            )
        ],
        responses={
            200: openapi.Response('Successfully retrieved bank accounts', PartnerBankAccountSerializer(many=True)),
            404: "Not Found: User or bank details not found",
            400: "Bad Request: Missing user information or user not recognized",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            user, error_response = self.resolve_read_partner(request)
            if error_response:
                return error_response

            # Retrieve bank accounts associated with the user
            bank_accounts = PartnerBankAccount.objects.filter(bank_account_for_partner=user)
            if bank_accounts.exists():
                serialized_accounts = PartnerBankAccountSerializer(bank_accounts, many=True)
                return Response(serialized_accounts.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Bank Account does not exist."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Get - ManagePartnerBankAccountView: {str(e)}")
            return Response({"message": "Failed to get user bank account. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'account_title': openapi.Schema(type=openapi.TYPE_STRING, description='Account title'),
                'account_number': openapi.Schema(type=openapi.TYPE_STRING, description='Account number'),
                'bank_name': openapi.Schema(type=openapi.TYPE_STRING, description='Bank name'),
                'branch_code': openapi.Schema(type=openapi.TYPE_STRING, description='Branch code')
            },
            required=['account_title', 'account_number', 'bank_name', 'branch_code']
        ),
        responses={
            201: openapi.Response("Bank account details added successfully", PartnerBankAccountSerializer),
            404: "Not Found: User not found",
            400: "Bad Request: Missing user information or user not recognized or invalid input data",
            409: "Conflict: Bank account details already exist",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            user = self.get_partner(request)
            data = self.get_request_data_without_partner_token(request)

            # Validate required fields
            required_fields = ['account_title', 'account_number', 'bank_name', 'branch_code']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Check if the bank account details already exist
            check_exist = PartnerBankAccount.objects.filter(
                bank_account_for_partner=user,
                account_title=data.get('account_title'),
                account_number=data.get('account_number'),
                bank_name=data.get('bank_name')
            ).first()
            if check_exist:
                return Response({"message": "This account detail already exists."}, status=status.HTTP_409_CONFLICT)

            data['bank_account_for_partner'] = user.partner_id
            serializer = PartnerBankAccountSerializer(data=data)
            if not serializer.is_valid():
                # Extracting first error message with field name
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": f"{first_error_message}"}, status=status.HTTP_400_BAD_REQUEST)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except AuthenticationFailed:
            raise
        except Exception as e:
            logger.error("Post - ManagePartnerBankAccountView error: %s", str(e))
            return Response({"message": "Failed to add user bank account. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'account_id': openapi.Schema(type=openapi.TYPE_STRING, description='ID of the account to be deleted')
            },
            required=['account_id']
        ),
        responses={
            200: "Bank account has been removed successfully.",
            400: "Bad Request: Missing required information.",
            404: "Not Found: User or account details not found",
            500: "Server Error: Internal server error"
        }
    )
    def delete(self, request, *args, **kwargs):
        try:
            user = self.get_partner(request)
            data = self.get_request_data_without_partner_token(request)
            account_id = data.get('account_id')
            if not account_id:
                return Response({"message": "Missing required information."}, status=status.HTTP_400_BAD_REQUEST)

            check_exist = PartnerBankAccount.objects.filter(bank_account_for_partner=user, account_id=account_id).first()
            if not check_exist:
                return Response({"message": "This account detail does not exist."}, status=status.HTTP_404_NOT_FOUND)

            check_exist.delete()
            return Response({"message": "Bank account has been removed successfully."}, status=status.HTTP_200_OK)

        except AuthenticationFailed:
            raise
        except Exception as e:
            logger.error("Delete - ManagePartnerBankAccountView error: %s", str(e))
            return Response({"message": "Failed to delete user bank account. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManagePartnerWithdrawView(PartnerHeaderAuthenticationAPIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_description=(
            "Retrieve withdrawal requests for the authenticated partner. "
            "Admin-session aliases may still supply `partner_session_token` through "
            "explicit compatibility helpers, but canonical operator requests use "
            "the Authorization header."
        ),
        manual_parameters=[
            openapi.Parameter(
                'partner_session_token',
                openapi.IN_QUERY,
                type=openapi.TYPE_STRING,
                required=False,
                description='Optional for documented legacy/admin compatibility only.',
            ),
            openapi.Parameter('status', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('from_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('to_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('sort', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
            openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
            openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
        ],
        responses={
            200: openapi.Response("Successful retrieval of withdrawal requests", PartnerWithdrawSerializer(many=True)),
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            user, error_response = self.resolve_read_partner(request)
            if error_response:
                return error_response

            queryset = (
                PartnerWithdraw.objects.filter(withdraw_for_partner=user)
                .select_related("withdraw_bank")
                .order_by("-request_time")
            )

            status_filter = _normalize_withdraw_status(request.GET.get("status"))
            if status_filter == "__invalid__":
                return Response(
                    {"message": "Invalid status filter. Allowed values: Pending, Processed, Completed, Rejected."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if status_filter:
                queryset = queryset.filter(withdraw_status=status_filter)

            search_query = (request.GET.get("search") or "").strip()
            if search_query:
                safe_query = search_query[:100]
                queryset = queryset.filter(
                    Q(withdraw_bank__account_title__icontains=safe_query)
                    | Q(withdraw_bank__account_number__icontains=safe_query)
                    | Q(withdraw_bank__bank_name__icontains=safe_query)
                )

            from_date = _parse_iso_date_param(request.GET.get("from_date"))
            if request.GET.get("from_date") and not from_date:
                return Response(
                    {"message": "Invalid from_date. Expected format: YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if from_date:
                queryset = queryset.filter(request_time__date__gte=from_date)

            to_date = _parse_iso_date_param(request.GET.get("to_date"))
            if request.GET.get("to_date") and not to_date:
                return Response(
                    {"message": "Invalid to_date. Expected format: YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if to_date:
                queryset = queryset.filter(request_time__date__lte=to_date)

            sort_order = _normalize_sort_param(
                request.GET.get("sort"),
                WITHDRAW_ALLOWED_SORTS,
                "-request_time",
            )
            queryset = queryset.order_by(sort_order, "-withdraw_id")

            paginator = WalletPagination()
            paginated_queryset = paginator.paginate_queryset(queryset, request)
            serialized_package = PartnerWithdrawSerializer(paginated_queryset, many=True)
            return paginator.get_paginated_response(serialized_package.data)

        except Exception as e:
            # Error adding in Logs file
            logger.error("Get - ManagePartnerWithdrawView: %s", str(e))
            return Response({"message": "Failed to get user withdraw history. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['account_id', 'withdraw_amount'],
            properties={
                'account_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='ID of the bank account for withdrawal'),
                'withdraw_amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Amount to withdraw')
            }
        ),
        responses={
            201: openapi.Response('Successfully created withdrawal request', PartnerWithdrawSerializer),
            400: 'Invalid request or missing required fields',
            409: 'Conflict in bank account details or insufficient wallet balance',
            500: 'Internal server error'
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            user = self.get_partner(request)
            data = self.get_request_data_without_partner_token(request)

            # Validate required fields
            required_fields = ['account_id', 'withdraw_amount']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Check if bank account details exist
            bank_detail = PartnerBankAccount.objects.filter(bank_account_for_partner=user, account_id=data.get('account_id')).first()
            if not bank_detail:
                return Response({"message": "Bank account details not found."}, status=status.HTTP_409_CONFLICT)

            # Check if user wallet exists and has sufficient balance
            user_wallet = Wallet.objects.filter(wallet_session=user).first()
            if not user_wallet:
                return Response({"message": "Wallet record not found."}, status=status.HTTP_400_BAD_REQUEST)

            if data.get('withdraw_amount') <= 0:
                return Response({"message": "Withdrawal amount must be greater than zero."}, status=status.HTTP_400_BAD_REQUEST)

            if user_wallet.wallet_amount < data.get('withdraw_amount'):
                return Response({"message": "Insufficient wallet balance for withdrawal request."}, status=status.HTTP_409_CONFLICT)

            backup_str = (f"Name: {user.name}, "
                          f"Phone Number: {user.country_code}{user.phone_number}, "
                          f"Bank Account #: {bank_detail.account_number}, "
                          f"Account Title: {bank_detail.account_title}, "
                          f"Bank Name: {bank_detail.bank_name}")

            # Prepare data for serializer
            data['withdraw_for_partner'] = user.partner_id
            data['withdraw_bank'] = bank_detail.account_id
            data['withdraw_status'] = "Pending"
            data['withdraw_backup_detail'] = backup_str

            # Serialize and save withdrawal request in a transaction
            serializer = PartnerWithdrawSerializer(data=data)
            if serializer.is_valid():
                with transaction.atomic():
                    serializer.save()
                    user_wallet.wallet_amount -= data.get('withdraw_amount')
                    user_wallet.save()
                return Response(serializer.data, status=status.HTTP_201_CREATED)
            else:
                # Extracting first error message with field name
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": first_error_message}, status=status.HTTP_400_BAD_REQUEST)

        except AuthenticationFailed:
            raise
        except Exception as e:
            # adding logs
            logger.error("Post - ManagePartnerWithdrawView: %s", str(e))
            return Response({"message": "Failed to add user withdraw request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnerAllTransactionHistoryView(PartnerHeaderAuthenticationAPIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Get Partner Transaction History",
        operation_description=(
            "Retrieve all transaction history for the authenticated partner. "
            "Admin-session aliases may still supply `partner_session_token` through "
            "explicit compatibility helpers, but canonical operator requests use "
            "the Authorization header."
        ),
        manual_parameters=[
                openapi.Parameter(
                    'partner_session_token',
                    openapi.IN_QUERY,
                    type=openapi.TYPE_STRING,
                    required=False,
                    description='Optional for documented legacy/admin compatibility only.',
                ),
                openapi.Parameter('transaction_type', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
                openapi.Parameter('search', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
                openapi.Parameter('from_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
                openapi.Parameter('to_date', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
                openapi.Parameter('sort', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=False),
                openapi.Parameter('page', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
                openapi.Parameter('page_size', openapi.IN_QUERY, type=openapi.TYPE_INTEGER, required=False),
        ],
        responses={
            200: openapi.Response("Successful retrieval of transaction history", PartnerTransactionSerializer(many=True)),
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            user, error_response = self.resolve_read_partner(request)
            if error_response:
                return error_response

            queryset = PartnerTransactionHistory.objects.filter(
                transaction_for_partner=user
            ).order_by('-transaction_time')

            normalized_transaction_type = _normalize_transaction_type(
                request.GET.get("transaction_type")
            )
            if normalized_transaction_type == "__invalid__":
                return Response(
                    {"message": "Invalid transaction_type. Allowed values: Credit, Debit."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if normalized_transaction_type:
                queryset = queryset.filter(transaction_type=normalized_transaction_type)

            search_query = (request.GET.get("search") or "").strip()
            if search_query:
                safe_query = search_query[:100]
                queryset = queryset.filter(
                    Q(transaction_code__icontains=safe_query)
                    | Q(transaction_description__icontains=safe_query)
                )

            from_date = _parse_iso_date_param(request.GET.get("from_date"))
            if request.GET.get("from_date") and not from_date:
                return Response(
                    {"message": "Invalid from_date. Expected format: YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if from_date:
                queryset = queryset.filter(transaction_time__date__gte=from_date)

            to_date = _parse_iso_date_param(request.GET.get("to_date"))
            if request.GET.get("to_date") and not to_date:
                return Response(
                    {"message": "Invalid to_date. Expected format: YYYY-MM-DD."},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            if to_date:
                queryset = queryset.filter(transaction_time__date__lte=to_date)

            sort_order = _normalize_sort_param(
                request.GET.get("sort"),
                TRANSACTION_ALLOWED_SORTS,
                "-transaction_time",
            )
            queryset = queryset.order_by(sort_order, "-transaction_id")

            paginator = WalletPagination()
            paginated_queryset = paginator.paginate_queryset(queryset, request)
            serialized_trans = PartnerTransactionSerializer(paginated_queryset, many=True)
            return paginator.get_paginated_response(serialized_trans.data)

        except Exception as e:
            # Error adding in Logs file
            logger.error("GetPartnerAllTransactionHistoryView error: %s", str(e))
            return Response({"message": "Failed to get user transaction history. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetPartnerTransactionOverallSummaryView(PartnerHeaderAuthenticationAPIView):
    permission_classes = [AllowAny]

    @swagger_auto_schema(
        operation_summary="Get Partner Transaction Counts and Amounts Summary",
        operation_description=(
            "Retrieve total credit and debit transaction amounts and counts for "
            "the authenticated partner. Admin-session aliases may still supply "
            "`partner_session_token` through explicit compatibility helpers, but "
            "canonical operator requests use the Authorization header."
        ),
        manual_parameters=[
            openapi.Parameter(
                'partner_session_token',
                openapi.IN_QUERY,
                description='Optional for documented legacy/admin compatibility only.',
                type=openapi.TYPE_STRING,
                required=False,
            )
        ],
        responses={
            200: openapi.Response("Successful retrieval of transaction amount summary"),
            404: "Not Found: User or transaction records not found",
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            user, error_response = self.resolve_read_partner(request)
            if error_response:
                return error_response

            # Aggregate total credit transaction amounts and counts for the user
            credit_transaction = PartnerTransactionHistory.objects.filter(
                transaction_for_partner=user,
                transaction_type="Credit"
            ).aggregate(total_amount=Sum('transaction_amount'), total_count=Count('transaction_id'))

            # Default values if no credit transactions found
            total_credit_amount = credit_transaction['total_amount'] if credit_transaction['total_amount'] is not None else 0
            total_credit_count = credit_transaction['total_count'] if credit_transaction['total_count'] is not None else 0

            # Aggregate total debit transaction amounts and counts for the user
            debit_transaction = PartnerTransactionHistory.objects.filter(
                transaction_for_partner=user,
                transaction_type="Debit"
            ).aggregate(total_debit_amounts=Sum('transaction_amount'), total_debit_counts=Count('transaction_id'))

            # Default values if no debit transactions found
            total_debit_amount = debit_transaction['total_debit_amounts'] if debit_transaction['total_debit_amounts'] is not None else 0
            total_debit_count = debit_transaction['total_debit_counts'] if debit_transaction['total_debit_counts'] is not None else 0

            # Prepare transaction summary dictionary
            transaction_summary = {
                'credit_transaction_amount': total_credit_amount,
                'debit_transaction_amount': total_debit_amount,
                'credit_number_transactions': total_credit_count,
                'debit_number_transactions': total_debit_count,
            }
            return Response(transaction_summary, status=status.HTTP_200_OK)

        except Exception as e:
            # Adding logs
            logger.error("GetPartnerTransactionOverallSummaryView error: %s", str(e))
            return Response({"message": "Failed to get user transaction overall summary. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
