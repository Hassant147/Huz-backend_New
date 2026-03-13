from django.db import transaction
from rest_framework import serializers, status
from rest_framework.response import Response
from rest_framework.views import APIView

from common.auth_utils import require_user_profile
from common.permissions import IsAdminOrAuthenticatedUserProfile

from .models import MailingDetail, UserBankAccount, UserTransactionHistory, UserWithdraw, Wallet
from .serializers import (
    MailingDetailSerializer,
    UserBankAccountSerializer,
    UserProfileSerializer,
    UserTransactionSerializer,
    UserWithdrawSerializer,
)


class CurrentUserProfileUpdateSerializer(serializers.Serializer):
    name = serializers.CharField(required=False, allow_blank=False, max_length=100)
    email = serializers.EmailField(required=False)
    user_gender = serializers.ChoiceField(
        choices=[choice[0] for choice in UserProfileSerializer.Meta.model.GENDER_CHOICES],
        required=False,
    )

    def validate(self, attrs):
        if not attrs:
            raise serializers.ValidationError({"message": "At least one profile field is required."})
        return attrs


class CurrentUserAddressUpsertSerializer(serializers.Serializer):
    address_id = serializers.UUIDField(required=False)
    street_address = serializers.CharField()
    address_line2 = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    city = serializers.CharField()
    state = serializers.CharField()
    country = serializers.CharField()
    postal_code = serializers.CharField()
    lat = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    long = serializers.CharField(required=False, allow_blank=True, allow_null=True)


class CurrentUserBankAccountCreateSerializer(serializers.Serializer):
    account_title = serializers.CharField()
    account_number = serializers.CharField()
    bank_name = serializers.CharField()
    branch_code = serializers.CharField()


class CurrentUserBankAccountDeleteSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()


class CurrentUserWithdrawCreateSerializer(serializers.Serializer):
    account_id = serializers.UUIDField()
    withdraw_amount = serializers.FloatField()


class CurrentUserProfileView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = require_user_profile(request)
        serializer = UserProfileSerializer(user)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def put(self, request):
        user = require_user_profile(request)
        input_serializer = CurrentUserProfileUpdateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        validated_data = input_serializer.validated_data

        if "name" in validated_data:
            user.name = validated_data["name"]
        if "email" in validated_data:
            user.email = validated_data["email"]
        if "user_gender" in validated_data:
            user.user_gender = validated_data["user_gender"]

        user.save(update_fields=[field for field in ("name", "email", "user_gender") if field in validated_data])
        serializer = UserProfileSerializer(user)
        return Response(serializer.data, status=status.HTTP_200_OK)


class CurrentUserAddressView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = require_user_profile(request)
        address_detail = MailingDetail.objects.filter(mailing_session=user).first()
        if not address_detail:
            return Response({"message": "Address detail not exist."}, status=status.HTTP_404_NOT_FOUND)

        serializer = MailingDetailSerializer(address_detail)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        user = require_user_profile(request)
        input_serializer = CurrentUserAddressUpsertSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)

        if MailingDetail.objects.filter(mailing_session=user).exists():
            return Response({"message": "Address detail already exists."}, status=status.HTTP_409_CONFLICT)

        serializer = MailingDetailSerializer(data=input_serializer.validated_data)
        serializer.is_valid(raise_exception=True)
        user.is_address_exist = True
        user.save(update_fields=["is_address_exist"])
        serializer.save(mailing_session=user)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def put(self, request):
        user = require_user_profile(request)
        input_serializer = CurrentUserAddressUpsertSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        validated_data = input_serializer.validated_data
        address_id = validated_data.get("address_id")
        if not address_id:
            return Response(
                {"message": "Missing user information or address ID."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        address_detail = MailingDetail.objects.filter(
            mailing_session=user,
            address_id=address_id,
        ).first()
        if not address_detail:
            return Response({"message": "Address detail not found."}, status=status.HTTP_404_NOT_FOUND)

        serializer = MailingDetailSerializer(address_detail, data=validated_data, partial=True)
        serializer.is_valid(raise_exception=True)
        serializer.save()
        if not user.is_address_exist:
            user.is_address_exist = True
            user.save(update_fields=["is_address_exist"])
        return Response(serializer.data, status=status.HTTP_200_OK)


class CurrentUserWalletBankAccountsView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = require_user_profile(request)
        bank_accounts = UserBankAccount.objects.filter(bank_account_for_user=user).order_by("-created_time")
        serializer = UserBankAccountSerializer(bank_accounts, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        user = require_user_profile(request)
        input_serializer = CurrentUserBankAccountCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        validated_data = input_serializer.validated_data

        check_exist = UserBankAccount.objects.filter(
            bank_account_for_user=user,
            account_title=validated_data["account_title"],
            account_number=validated_data["account_number"],
            bank_name=validated_data["bank_name"],
        ).first()
        if check_exist:
            return Response({"message": "This account detail already exists."}, status=status.HTTP_409_CONFLICT)

        serializer = UserBankAccountSerializer(
            data={
                **validated_data,
                "bank_account_for_user": user.user_id,
            }
        )
        serializer.is_valid(raise_exception=True)
        serializer.save()
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    def delete(self, request):
        user = require_user_profile(request)
        input_serializer = CurrentUserBankAccountDeleteSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        account_id = input_serializer.validated_data["account_id"]

        bank_account = UserBankAccount.objects.filter(
            bank_account_for_user=user,
            account_id=account_id,
        ).first()
        if not bank_account:
            return Response({"message": "This account detail does not exist."}, status=status.HTTP_404_NOT_FOUND)

        bank_account.delete()
        return Response(
            {"message": "Bank account has been removed successfully."},
            status=status.HTTP_200_OK,
        )


class CurrentUserWalletWithdrawalsView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = require_user_profile(request)
        withdrawals = UserWithdraw.objects.filter(withdraw_for_user=user).order_by("-request_time")
        serializer = UserWithdrawSerializer(withdrawals, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        user = require_user_profile(request)
        input_serializer = CurrentUserWithdrawCreateSerializer(data=request.data)
        input_serializer.is_valid(raise_exception=True)
        validated_data = input_serializer.validated_data

        bank_detail = UserBankAccount.objects.filter(
            bank_account_for_user=user,
            account_id=validated_data["account_id"],
        ).first()
        if not bank_detail:
            return Response({"message": "Bank account details not found."}, status=status.HTTP_409_CONFLICT)

        user_wallet = Wallet.objects.filter(wallet_session=user).first()
        if not user_wallet:
            return Response({"message": "Wallet record not found."}, status=status.HTTP_400_BAD_REQUEST)

        withdraw_amount = validated_data["withdraw_amount"]
        if withdraw_amount <= 0:
            return Response(
                {"message": "Withdrawal amount must be greater than zero."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if user_wallet.wallet_amount < withdraw_amount:
            return Response(
                {"message": "Insufficient wallet balance for withdrawal request."},
                status=status.HTTP_409_CONFLICT,
            )

        backup_str = (
            f"Name: {user.name}, "
            f"Phone Number: {user.country_code}{user.phone_number}, "
            f"Bank Account #: {bank_detail.account_number}, "
            f"Account Title: {bank_detail.account_title}, "
            f"Bank Name: {bank_detail.bank_name}"
        )

        with transaction.atomic():
            serializer = UserWithdrawSerializer(
                data={
                    "withdraw_for_user": user.user_id,
                    "withdraw_bank": bank_detail.account_id,
                    "withdraw_amount": withdraw_amount,
                    "withdraw_status": "Pending",
                    "withdraw_backup_detail": backup_str,
                }
            )
            serializer.is_valid(raise_exception=True)
            serializer.save()
            user_wallet.wallet_amount -= withdraw_amount
            user_wallet.save(update_fields=["wallet_amount", "last_update_time"])

        return Response(serializer.data, status=status.HTTP_201_CREATED)


class CurrentUserWalletTransactionsView(APIView):
    permission_classes = [IsAdminOrAuthenticatedUserProfile]

    def get(self, request):
        user = require_user_profile(request)
        transactions = UserTransactionHistory.objects.filter(
            transaction_for_user=user
        ).order_by("-transaction_time")
        serializer = UserTransactionSerializer(transactions, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)
