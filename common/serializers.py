from rest_framework import serializers
from .models import UserOTP, SubscribeUser, UserProfile, Wallet, MailingDetail, UserBankAccount, UserTransactionHistory, UserWithdraw
import re


class SubscribeSerializer(serializers.ModelSerializer):

    class Meta:
        model = SubscribeUser
        fields = ['email']

    def validate_email(self, value):
        # Regular expression pattern for a valid phone number
        regex = r'^[a-zA-Z0-9.+_-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]+$'
        # Validate the phone number
        if not re.fullmatch(regex, value):
            raise serializers.ValidationError("You've entered an invalid email format.")
        return value


class UserOTPSerializer(serializers.ModelSerializer):
    class Meta:
        model = UserOTP
        fields = ['phone_number']

    def validate_phone_number(self, value):
        # Regular expression pattern for a valid phone number
        regex = r'^(\+\d{1,3}[\s-]?)?\d{10}$'
        # Validate the phone number
        if not re.fullmatch(regex, value):
            raise serializers.ValidationError("You've entered an invalid Phone Number.")
        return value


class UserProfileSerializer(serializers.ModelSerializer):
    wallet_amount = serializers.SerializerMethodField()

    class Meta:
        model = UserProfile
        fields = (
            'session_token', 'name', 'country_code', 'phone_number', 'email', 'user_gender', 'user_type',
            'firebase_token', 'web_firebase_token', 'is_email_verified', 'is_address_exist',
            'is_notification_allowed', 'user_photo', 'account_status', 'wallet_amount'
                  )

    def get_wallet_amount(self, obj):
        return Wallet.objects.values_list('wallet_amount').get(wallet_session=obj)[0]

    def validate_phone_number(self, value):
        # Regular expression pattern for a valid phone number
        regex = r'^(\+\d{1,3}[\s-]?)?\d{10}$'
        # Validate the phone number
        if not re.fullmatch(regex, value):
            raise serializers.ValidationError("You've entered an invalid Phone Number.")
        return value

    def validate_email(self, value):
        # Regular expression pattern for a valid phone number
        regex = r'^[a-zA-Z0-9.+_-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]+$'
        # Validate the phone number
        if not re.fullmatch(regex, value):
            raise serializers.ValidationError("You've entered an invalid email format.")
        return value


class MailingDetailSerializer(serializers.ModelSerializer):

    class Meta:
        model = MailingDetail
        fields = ('address_id', 'street_address', 'address_line2', 'city', 'state', 'country', 'postal_code', 'lat', 'long')

    def validate_postal_code(self, value):
        # validation:
        if len(value) > 10:
            raise serializers.ValidationError("Postal code must be 6 characters long.")
        return value

    def to_representation(self, instance):
        data = super().to_representation(instance)
        # response transformation:
        data['full_address'] = f"{data['street_address']}, {data['city']}, {data['state']}, {data['country']} - {data['postal_code']}"
        return data


class UserBankAccountSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserBankAccount
        fields = ['account_id', 'account_title', 'account_number', 'bank_name', 'branch_code', 'created_time', 'bank_account_for_user']


class UserWithdrawSerializer(serializers.ModelSerializer):
    # Getting Nested fields using source and read_only
    account_title = serializers.CharField(source='withdraw_bank.account_title', read_only=True)
    account_number = serializers.CharField(source='withdraw_bank.account_number', read_only=True)
    bank_name = serializers.CharField(source='withdraw_bank.bank_name', read_only=True)

    class Meta:
        model = UserWithdraw
        fields = ['account_title', 'account_number', 'bank_name', 'withdraw_amount', 'request_time', 'withdraw_status', 'process_time', 'withdraw_for_user', 'withdraw_bank', 'withdraw_backup_detail']


class UserTransactionSerializer(serializers.ModelSerializer):

    class Meta:
        model = UserTransactionHistory
        fields = ['transaction_id', 'transaction_code', 'transaction_amount', 'transaction_type', 'transaction_time', 'transaction_description']