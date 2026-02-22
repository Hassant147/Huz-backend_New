import json
from uuid import UUID
from datetime import datetime
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from .models import ChatMessage, UserProfile, PartnerProfile
from django.db.models import OuterRef, Subquery, F, Window
from django.db.models.functions import Rank
from common.logs_file import logger
from .serializer import get_company_detail


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def json_serializer(data):
    return json.dumps(data, cls=UUIDEncoder)


class ChatConsumer(AsyncWebsocketConsumer):
    # Track active connections
    active_users = {}  # {user_id: [channel_names]}
    active_partners = {}  # {partner_id: [channel_names]}

    async def connect(self):
        try:
            await self.accept()
            self.user = None
            self.partner = None
            self.user_id = None
            self.partner_id = None
        except Exception as e:
            logger.error(f"Connection error: {str(e)}")
            await self.close(code=4001)

    async def disconnect(self, close_code):
        if self.user:
            await self.update_user_status(self.user_id, False)
            if self.user_id in self.active_users:
                self.active_users[self.user_id].remove(self.channel_name)
                if not self.active_users[self.user_id]:
                    del self.active_users[self.user_id]
        elif self.partner:
            await self.update_partner_status(self.partner_id, False)
            if self.partner_id in self.active_partners:
                self.active_partners[self.partner_id].remove(self.channel_name)
                if not self.active_partners[self.partner_id]:
                    del self.active_partners[self.partner_id]

        if self.user or self.partner:
            room_name = f"user_{self.user_id}_partner_{self.partner_id}"
            await self.channel_layer.group_discard(room_name, self.channel_name)

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            action = data.get('action')

            if action == 'authenticate':
                await self.handle_authentication(data)
            elif action == 'send_message':
                await self.handle_send_message(data)
            elif action == 'get_inbox':
                await self.handle_get_inbox(data)
            elif action == 'get_messages':
                await self.handle_get_messages(data)

        except Exception as e:
            await self.send_error(str(e))

    async def handle_authentication(self, data):
        user_token = data.get('user_session_token')
        partner_token = data.get('partner_session_token')

        if user_token:
            self.user = await self.get_user_profile(user_token)
            if not self.user:
                raise Exception("User not found")
            self.user_id = str(self.user.user_id)
            await self.update_user_status(self.user_id, True)
            self.active_users[self.user_id] = self.active_users.get(self.user_id, []) + [self.channel_name]
        elif partner_token:
            self.partner = await self.get_partner_profile(partner_token)
            if not self.partner:
                raise Exception("Partner not found")
            self.partner_id = str(self.partner.partner_id)
            await self.update_partner_status(self.partner_id, True)
            self.active_partners[self.partner_id] = self.active_partners.get(self.partner_id, []) + [self.channel_name]
        else:
            raise Exception("Missing authentication token")

        await self.send(json_serializer({
            'status': 'authenticated',
            'user_id': self.user_id,
            'partner_id': self.partner_id,
            'online': True  # Indicate that the authenticated user/partner is online
        }))

    async def update_user_status(self, user_id, is_online):
        """Update user's online status and broadcast it to relevant partners."""
        # await self.set_user_online_status(user_id, is_online)
        room_name = f"user_{user_id}"
        await self.channel_layer.group_send(
            room_name,
            {
                "type": "user_status",
                "user_id": user_id,
                "online": is_online
            }
        )

    async def update_partner_status(self, partner_id, is_online):
        """Update partner's online status and broadcast it to relevant users."""
        # await self.set_partner_online_status(partner_id, is_online)
        room_name = f"partner_{partner_id}"
        await self.channel_layer.group_send(
            room_name,
            {
                "type": "partner_status",
                "partner_id": partner_id,
                "online": is_online
            }
        )

    async def user_status(self, event):
        """Handle user status updates."""
        await self.send(json_serializer({
            'action': 'user_status',
            'user_id': event['user_id'],
            'online': event['online']
        }))

    async def partner_status(self, event):
        """Handle partner status updates."""
        await self.send(json_serializer({
            'action': 'partner_status',
            'partner_id': event['partner_id'],
            'online': event['online']
        }))

    async def handle_send_message(self, data):
        if not (self.user or self.partner):
            raise Exception("Not authenticated")

        # Validate fields based on authenticated entity
        if self.user:
            required_fields = ['user_id', 'partner_id', 'message']
            sender_type = 'user'
        else:
            required_fields = ['partner_id', 'user_id', 'message']
            sender_type = 'partner'

        for field in required_fields:
            if not data.get(field):
                raise Exception(f"Missing {field}")

        # Verify authorization
        if self.user and data['user_id'] != self.user_id:
            raise Exception("Unauthorized user_id")
        if self.partner and data['partner_id'] != self.partner_id:
            raise Exception("Unauthorized partner_id")

        # Get message participants
        user = self.user or await self.get_user_by_id(data['user_id'])
        partner = self.partner or await self.get_partner_by_id(data['partner_id'])

        if not user or not partner:
            raise Exception("Invalid user or partner")

        # Create and broadcast message
        message = await self.create_message(
            user=user,
            partner=partner,
            sender=sender_type,
            message=data['message']
        )

        room_name = f"user_{user.user_id}_partner_{partner.partner_id}"
        await self.channel_layer.group_add(room_name, self.channel_name)

        # Serialize the message synchronously
        serialized_message = self.serialize_message(message)

        await self.channel_layer.group_send(
            room_name,
            {
                "type": "chat_message",
                "message": serialized_message,
                "tempId": data.get("tempId"),
                "id": str(message.id)
            }
        )

    async def handle_get_inbox(self, data):
        if self.user:
            if data.get('user_id') != self.user_id:
                raise Exception("Unauthorized user_id")
            messages = await self.get_user_inbox(self.user)
        elif self.partner:
            if data.get('partner_id') != self.partner_id:
                raise Exception("Unauthorized partner_id")
            messages = await self.get_partner_inbox(self.partner)
        else:
            raise Exception("Not authenticated")

        await self.send(json_serializer({'inbox': messages}))

    async def handle_get_messages(self, data):
        if self.user:
            required_fields = ['user_id', 'partner_id']
            user = self.user
            partner = await self.get_partner_by_id(data['partner_id'])
        elif self.partner:
            required_fields = ['partner_id', 'user_id']
            partner = self.partner
            user = await self.get_user_by_id(data['user_id'])
        else:
            raise Exception("Not authenticated")

        for field in required_fields:
            if not data.get(field):
                raise Exception(f"Missing {field}")
        if data.get('user_id', '') != str(getattr(user, 'user_id', '')):
            raise Exception("Unauthorized user_id")
        if data.get('partner_id', '') != str(getattr(partner, 'partner_id', '')):
            raise Exception("Unauthorized partner_id")

        if self.user:
            await self.mark_messages_as_read(user, partner, 'partner')
        elif self.partner:
            await self.mark_messages_as_read(user, partner, 'user')

        messages = await self.get_chat_messages(user, partner)
        await self.send(json_serializer({'messages': messages}))

    async def chat_message(self, event):
        await self.send(text_data=json_serializer(event['message']))

    @database_sync_to_async
    def mark_messages_as_read(self, user, partner, sender):
        ChatMessage.objects.filter(
            user=user,
            partner=partner,
            sender=sender,
            is_read=False
        ).update(is_read=True)

    # Database operations
    @database_sync_to_async
    def get_user_profile(self, session_token):
        return UserProfile.objects.filter(session_token=session_token).first()

    @database_sync_to_async
    def get_partner_profile(self, session_token):
        return PartnerProfile.objects.filter(partner_session_token=session_token).first()

    @database_sync_to_async
    def get_user_by_id(self, user_id):
        return UserProfile.objects.filter(user_id=user_id).first()

    @database_sync_to_async
    def get_partner_by_id(self, partner_id):
        return PartnerProfile.objects.filter(partner_id=partner_id).first()

    @database_sync_to_async
    def create_message(self, user, partner, sender, message):
        new_message = ChatMessage.objects.create(
            user=user,
            partner=partner,
            sender=sender,
            message=message,
            is_read=False
        )
        return new_message

    @database_sync_to_async
    def get_user_inbox(self, user):
        messages = ChatMessage.objects.filter(
            user=user
        ).annotate(
            rank=Window(
                expression=Rank(),
                partition_by=[F('partner')],
                order_by=F('date').desc()
            )
        ).filter(rank=1).select_related('partner')

        inbox_data = []
        for msg in messages:
            # Get company details (name and logo) for partner if applicable
            company_details = get_company_detail(msg)

            inbox_data.append({
                'id': str(msg.id),
                'partner_id': str(msg.partner.partner_id),
                'partner_name': msg.partner.name,
                'partner_session_token': msg.partner.partner_session_token,  # partner session token
                'last_message': msg.message,
                'timestamp': msg.date.isoformat(),
                'unread_count': ChatMessage.objects.filter(
                    user=user,
                    partner=msg.partner,
                    sender='partner',
                    is_read=False
                ).count(),
                'company_name': company_details.get('company_name') if company_details else None,  # Add company name
                'company_logo': company_details.get('company_logo') if company_details else None  # Add company logo
            })

        return inbox_data

    @database_sync_to_async
    def get_partner_inbox(self, partner):
        messages = ChatMessage.objects.filter(
            partner=partner
        ).annotate(
            rank=Window(
                expression=Rank(),
                partition_by=[F('user')],
                order_by=F('date').desc()
            )
        ).filter(rank=1).select_related('user')

        inbox_data = []
        for msg in messages:
            user_photo_url = msg.user.user_photo.url if msg.user.user_photo else None  # Convert to URL

            inbox_data.append({
                'id': str(msg.id),
                'user_id': str(msg.user.user_id),  # user_id
                'user_fullname': msg.user.name,  # user_fullname
                'user_photo': user_photo_url,  # user_photo as URL
                'user_name': msg.user.name,  # user_name if needed as well
                'last_message': msg.message,
                'timestamp': msg.date.isoformat(),
                'unread_count': ChatMessage.objects.filter(
                    partner=partner,
                    user=msg.user,
                    sender='user',
                    is_read=False
                ).count()
            })

        return inbox_data

    @database_sync_to_async
    def get_chat_messages(self, user, partner):
        """Retrieve chat history between user and partner"""
        messages = ChatMessage.objects.filter(
            user=user,
            partner=partner
        ).order_by('date')

        return [{
            'id': str(msg.id),
            'sender': msg.sender,
            'message': msg.message,
            'timestamp': msg.date.isoformat(),
            'is_read': msg.is_read
        } for msg in messages]

    def serialize_message(self, message):
        """Serialize message object to JSON-serializable format"""
        return {
            'id': str(message.id),
            'sender': message.sender,
            'message': message.message,
            'timestamp': message.date.isoformat(),
            'user_id': str(message.user.user_id),
            'partner_id': str(message.partner.partner_id),
            'is_read': message.is_read
        }

    async def send_error(self, message):
        await self.send(json_serializer({
            'status': 'error',
            'message': message
        }))