from rest_framework import serializers


class AdminLoginRequestSerializer(serializers.Serializer):
    username = serializers.CharField()
    password = serializers.CharField()


class AdminSessionUserSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    username = serializers.CharField(read_only=True)
    email = serializers.EmailField(read_only=True, allow_blank=True)
    name = serializers.SerializerMethodField()
    role = serializers.SerializerMethodField()

    def get_name(self, obj):
        full_name = str(getattr(obj, "get_full_name", lambda: "")() or "").strip()
        return full_name or str(getattr(obj, "username", "") or "")

    def get_role(self, obj):
        return "admin"


class AdminSessionEnvelopeSerializer(serializers.Serializer):
    # Keep the admin bootstrap envelope stable so the frontend can treat the
    # backend session as the source of truth for admin identity.
    authenticated = serializers.BooleanField()
    user = AdminSessionUserSerializer(allow_null=True)
    message = serializers.CharField(required=False, allow_blank=True, allow_null=True)
    csrf_token = serializers.CharField(required=False, allow_blank=True, allow_null=True)
