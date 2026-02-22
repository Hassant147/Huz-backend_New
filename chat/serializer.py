from rest_framework import serializers
from .models import ChatMessage
from partners.models import BusinessProfile
from partners.serializers import ShortBusinessSerializer


def get_company_detail(obj):
    if obj.partner.partner_type == "Company":
        try:
            company_detail = BusinessProfile.objects.get(company_of_partner=obj.partner.partner_id)
            return ShortBusinessSerializer(company_detail).data
        except BusinessProfile.DoesNotExist:
            return None
    else:
        return None


class MessageSerializer(serializers.ModelSerializer):
    user_session_token = serializers.CharField(source='user.session_token', read_only=True)
    user_id = serializers.CharField(source='user.user_id', read_only=True)
    user_fullname = serializers.CharField(source='user.name', read_only=True)
    user_photo = serializers.CharField(source='user.user_photo', read_only=True)

    partner_session_token = serializers.CharField(source='partner.partner_session_token', read_only=True)
    partner_id = serializers.CharField(source='partner.partner_id', read_only=True)
    company_detail = serializers.SerializerMethodField()

    class Meta:
        model = ChatMessage
        fields = ['id',
                  'user_id', 'user_session_token', 'user_fullname', 'user_photo',
                  'partner_id', 'partner_session_token', 'company_detail',
                  'sender', 'message', 'is_read', 'date']

    def get_company_detail(self, obj):
        return get_company_detail(obj)

    def __init__(self, *args, **kwargs):
        super(MessageSerializer, self).__init__(*args, **kwargs)
        request = self.context.get('request')
        if request and request.method == 'POST':
            self.Meta.depth = 0
        else:
            self.Meta.depth = 2