from rest_framework.views import APIView
from rest_framework.permissions import IsAdminUser
from rest_framework.response import Response
from rest_framework import status
from .models import UserProfile, UserWithdraw, UserBankAccount, Wallet, UserTransactionHistory
from .serializers import UserWithdrawSerializer, UserBankAccountSerializer, UserTransactionSerializer
from .utility import validate_required_fields
from .logs_file import logger
from django.db import transaction
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi
from django.db.models import Sum, Count


class ManageBankAccountView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_QUERY, description="Session token of the user", type=openapi.TYPE_STRING, required=True)
        ],
        responses={
            200: openapi.Response('Successfully retrieved bank accounts', UserBankAccountSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or bank details not found",
            400: "Bad Request: Missing user information or user not recognized",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            session_token = request.GET.get('session_token')
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user based on session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve bank accounts associated with the user
            bank_accounts = UserBankAccount.objects.filter(bank_account_for_user=user)
            if bank_accounts.exists():
                serialized_accounts = UserBankAccountSerializer(bank_accounts, many=True)
                return Response(serialized_accounts.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Bank Account does not exist."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            logger.error(f"Get - ManageBankAccountView: {str(e)}")
            return Response({"message": "Failed to get user bank account. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'account_title': openapi.Schema(type=openapi.TYPE_STRING, description='Account title'),
                'account_number': openapi.Schema(type=openapi.TYPE_STRING, description='Account number'),
                'bank_name': openapi.Schema(type=openapi.TYPE_STRING, description='Bank name'),
                'branch_code': openapi.Schema(type=openapi.TYPE_STRING, description='Branch code')
            },
            required=['session_token', 'account_title', 'account_number', 'bank_name', 'branch_code']
        ),
        responses={
            201: openapi.Response("Bank account details added successfully", UserBankAccountSerializer),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User not found",
            400: "Bad Request: Missing user information or user not recognized or invalid input data",
            409: "Conflict: Bank account details already exist",
            500: "Server Error: Internal server error"
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            # Validate session_token presence
            data = request.data
            session_token = request.data.get('session_token')
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate required fields
            required_fields = ['account_title', 'account_number', 'bank_name', 'branch_code']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve user based on session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Check if the bank account details already exist
            check_exist = UserBankAccount.objects.filter(
                bank_account_for_user=user,
                account_title=request.data.get('account_title'),
                account_number=request.data.get('account_number'),
                bank_name=request.data.get('bank_name')
            ).first()
            if check_exist:
                return Response({"message": "This account detail already exists."}, status=status.HTTP_409_CONFLICT)

            data.pop('session_token')
            data['bank_account_for_user'] = user.user_id
            serializer = UserBankAccountSerializer(data=data)
            if not serializer.is_valid():
                # Extracting first error message with field name
                first_error_field = next(iter(serializer.errors))
                first_error_message = f"{first_error_field}: {serializer.errors[first_error_field][0]}"
                return Response({"message": f"{first_error_message}"}, status=status.HTTP_400_BAD_REQUEST)
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error("Post - ManageBankAccountView error: %s", str(e))
            return Response({"message": "Failed to add user bank account. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'account_id': openapi.Schema(type=openapi.TYPE_STRING, description='ID of the account to be deleted')
            },
            required=['session_token', 'account_id']
        ),
        responses={
            200: "Bank account has been removed successfully.",
            400: "Bad Request: Missing required information.",
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or account details not found",
            500: "Server Error: Internal server error"
        }
    )
    def delete(self, request, *args, **kwargs):
        try:
            session_token = request.data.get('session_token')
            account_id = request.data.get('account_id')
            if not session_token or not account_id:
                return Response({"message": "Missing required information."}, status=status.HTTP_400_BAD_REQUEST)

            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            check_exist = UserBankAccount.objects.filter(bank_account_for_user=user, account_id=account_id).first()
            if not check_exist:
                return Response({"message": "This account detail does not exist."}, status=status.HTTP_404_NOT_FOUND)

            check_exist.delete()
            return Response({"message": "Bank account has been removed successfully."}, status=status.HTTP_200_OK)

        except Exception as e:
            logger.error("Delete - ManageBankAccountView error: %s", str(e))
            return Response({"message": "Failed to delete user bank account. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class ManageWithdrawView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        manual_parameters=[
            openapi.Parameter('session_token', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True, description='Session token of the user')
        ],
        responses={
            200: openapi.Response("Successful retrieval of withdrawal requests", UserWithdrawSerializer),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or withdrawal requests not found",
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            # Check if session_token exist
            session_token = self.request.GET.get('session_token', None)
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user based on session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Check if there are withdrawal requests for the user
            check_exist = UserWithdraw.objects.filter(withdraw_for_user=user)
            if check_exist.exists():
                serialized_package = UserWithdrawSerializer(check_exist, many=True)
                return Response(serialized_package.data, status=status.HTTP_200_OK)
            else:
                return Response({"message": "Withdraw request not exist."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            # Error adding in Logs file
            logger.error("Get - ManageWithdrawView: %s", str(e))
            return Response({"message": "Failed to get user withdraw history. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @swagger_auto_schema(
        request_body=openapi.Schema(
            type=openapi.TYPE_OBJECT,
            required=['session_token', 'account_id', 'withdraw_amount'],
            properties={
                'session_token': openapi.Schema(type=openapi.TYPE_STRING, description='Session token of the user'),
                'account_id': openapi.Schema(type=openapi.TYPE_INTEGER, description='ID of the bank account for withdrawal'),
                'withdraw_amount': openapi.Schema(type=openapi.TYPE_NUMBER, description='Amount to withdraw')
            }
        ),
        responses={
            status.HTTP_201_CREATED: openapi.Response('Successfully created withdrawal request',
                                                      UserWithdrawSerializer),
            status.HTTP_400_BAD_REQUEST: 'Invalid request or missing required fields',
            status.HTTP_409_CONFLICT: 'Conflict in bank account details or insufficient wallet balance',
            status.HTTP_500_INTERNAL_SERVER_ERROR: 'Unexpected server error occurred'
        }
    )
    def post(self, request, *args, **kwargs):
        try:
            data = request.data
            session_token = data.get('session_token')

            # Check if session_token is provided
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Validate required fields
            required_fields = ['account_id', 'withdraw_amount']
            error_response = validate_required_fields(required_fields, data)
            if error_response:
                return error_response

            # Retrieve user based on session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."},  status=status.HTTP_400_BAD_REQUEST)

            # Check if bank account details exist
            bank_detail = UserBankAccount.objects.filter(bank_account_for_user=user, account_id=data.get('account_id')).first()
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
                          f"Phone Number: {user.country_code, user.phone_number}, "
                          f"Session Token: {user.session_token}, "
                          f"Bank Account #: {bank_detail.account_number}, "
                          f"Account Title: {bank_detail.account_title}, "
                          f"Bank Name: {bank_detail.bank_name}")

            # Prepare data for serializer
            data.pop('session_token')
            data['withdraw_for_user'] = user.user_id
            data['withdraw_bank'] = bank_detail.account_id
            data['withdraw_status'] = "Pending"
            data['withdraw_backup_detail'] = backup_str

            # Serialize and save withdrawal request in a transaction
            serializer = UserWithdrawSerializer(data=data)
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

        except Exception as e:
            # adding logs
            logger.error("Post - ManageWithdrawView: %s", str(e))
            return Response({"message": "Failed to add user withdraw request. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetUserAllTransactionHistoryView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_summary="Get User Transaction History",
        operation_description="Retrieve all transaction history for a user based on session token",
        manual_parameters=[openapi.Parameter('session_token', openapi.IN_QUERY, type=openapi.TYPE_STRING, required=True, description='Session token of the user')],
        responses={
            200: openapi.Response("Successful retrieval of transaction history", UserTransactionSerializer(many=True)),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or transaction history not found",
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            # Check if session_token exist
            session_token = request.GET.get('session_token')
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user based on session_token
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Retrieve user transactions based on user session_token
            exist_trans = UserTransactionHistory.objects.filter(
                transaction_for_user=user
            ).order_by('-transaction_time')

            if not exist_trans.exists():
                return Response({"message": "Transaction records not found."}, status=status.HTTP_404_NOT_FOUND)

            serialized_trans = UserTransactionSerializer(exist_trans, many=True)
            return Response(serialized_trans.data, status=status.HTTP_200_OK)

        except Exception as e:
            # Error adding in Logs file
            logger.error("GetUserAllTransactionHistory error: %s", str(e))
            return Response({"message": "Failed to get user transaction history. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class GetUserTransactionOverallSummaryView(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_summary="Get User Transaction Counts and Amounts Summary",
        operation_description="Retrieve the total credit and debit transaction amounts and counts for a user based on session token",
        manual_parameters=[openapi.Parameter('session_token', openapi.IN_QUERY, description="Session token of the user", type=openapi.TYPE_STRING, required=True)],
        responses={
            200: openapi.Response("Successful retrieval of transaction amount summary"),
            401: "Unauthorized: Admin permissions required",
            404: "Not Found: User or transaction records not found",
            400: "Bad Request: Invalid input data",
            500: "Server Error: Internal server error"
        }
    )
    def get(self, request):
        try:
            # Check if session_token is provided
            session_token = request.GET.get('session_token')
            if not session_token:
                return Response({"message": "Missing user information."}, status=status.HTTP_400_BAD_REQUEST)

            # Retrieve user based on session_token and checking if user exists
            user = UserProfile.objects.filter(session_token=session_token).first()
            if not user:
                return Response({"message": "User not found with the provided detail."}, status=status.HTTP_404_NOT_FOUND)

            # Aggregate total credit transaction amounts and counts for the user
            credit_transaction = UserTransactionHistory.objects.filter(
                transaction_for_user=user,
                transaction_type="Credit"
            ).aggregate(total_amount=Sum('transaction_amount'), total_count=Count('transaction_id'))

            # Default values if no credit transactions found
            total_credit_amount = credit_transaction['total_amount'] if credit_transaction['total_amount'] is not None else 0
            total_credit_count = credit_transaction['total_count'] if credit_transaction['total_count'] is not None else 0

            # Aggregate total debit transaction amounts and counts for the user
            debit_transaction = UserTransactionHistory.objects.filter(
                transaction_for_user=user,
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
            logger.error("GetUserAllTransactionAmount error: %s", str(e))
            return Response({"message": "Failed to get user transaction overall summary. Internal server error."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
