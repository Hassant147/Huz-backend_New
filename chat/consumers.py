import json
import asyncio
from uuid import UUID
from datetime import datetime
from typing import Optional, Dict, Set, List
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.db.models import OuterRef, Subquery, F, Window, Count, IntegerField, Q
from django.core.paginator import Paginator
from django.core.exceptions import ObjectDoesNotExist
from django.utils import timezone
from .models import ChatMessage, UserProfile, PartnerProfile
from common.logs_file import logger
from .serializer import get_company_detail
from django.db.models.functions import Rank


class UUIDEncoder(json.JSONEncoder):
    def default(self, obj):
        if isinstance(obj, UUID):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


def json_serializer(data):
    return json.dumps(data, cls=UUIDEncoder)


class RateLimiter:
    def __init__(self, max_requests: int, period: int):
        self.max_requests = max_requests
        self.period = period
        self.history: Dict[str, list] = {}

    async def check_rate_limit(self, key: str):
        now = timezone.now().timestamp()
        requests = self.history.get(key, [])
        requests = [t for t in requests if now - t < self.period]

        if len(requests) >= self.max_requests:
            return False

        requests.append(now)
        self.history[key] = requests
        return True


class ChatConsumer(AsyncWebsocketConsumer):
    active_users: Dict[str, Set[str]] = {}
    active_partners: Dict[str, Set[str]] = {}
    users_lock = asyncio.Lock()
    partners_lock = asyncio.Lock()
    message_limiter = RateLimiter(max_requests=30, period=10)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.user: Optional[UserProfile] = None
        self.partner: Optional[PartnerProfile] = None
        self.user_id: Optional[str] = None
        self.partner_id: Optional[str] = None
        self.active_groups: Set[str] = set()

    # Core WebSocket Methods
    async def connect(self):
        try:
            await self.accept()
        except Exception as e:
            logger.error(f"Connection error: {str(e)}", exc_info=True)
            await self.close(code=4001)

    async def disconnect(self, close_code):
        await self.cleanup_presence()
        await self.leave_all_groups()

    async def receive(self, text_data):
        try:
            data = json.loads(text_data)
            action = data.get('action')

            if not await self.message_limiter.check_rate_limit(self.get_rate_limit_key()):
                await self.send_error("Rate limit exceeded", code=429)
                return

            handlers = {
                'authenticate': self.handle_authentication,
                'send_message': self.handle_send_message,
                'get_inbox': self.handle_get_inbox,
                'get_messages': self.handle_get_messages,
                'typing': self.handle_typing,
                'mark_read': self.handle_mark_read,
                'message_delivered': self.handle_message_delivered,
                'message_read': self.handle_message_read,
                'message_seen': self.handle_message_seen,
                'join_presence_group': self.join_presence_group,
            }

            if handler := handlers.get(action):
                await handler(data)
            else:
                await self.send_error("Invalid action requested", code=400)

        except json.JSONDecodeError:
            await self.send_error("Invalid JSON format", code=400)
        except Exception as e:
            logger.error(f"Error processing message: {str(e)}", exc_info=True)
            await self.send_error(str(e), code=500)

    # Authentication and Presence Management
    async def handle_authentication(self, data):
        try:
            if 'partner_session_token' in data:
                partner_token = data['partner_session_token']
                self.partner = await self.get_partner_profile(partner_token)
                if not self.partner:
                    raise ObjectDoesNotExist("Partner not found")
                self.partner_id = str(self.partner.partner_id)

                async with self.partners_lock:
                    self.active_partners.setdefault(self.partner_id, set()).add(self.channel_name)

                await self.send(json.dumps({
                    'action': 'authenticated',
                    'partner_id': self.partner_id,
                    'timestamp': datetime.now().isoformat()
                }))

            elif 'user_session_token' in data:
                user_token = data['user_session_token']
                self.user = await self.get_user_profile(user_token)
                if not self.user:
                    raise ObjectDoesNotExist("User not found")
                self.user_id = str(self.user.user_id)

                async with self.users_lock:
                    self.active_users.setdefault(self.user_id, set()).add(self.channel_name)

                await self.send(json.dumps({
                    'action': 'authenticated',
                    'user_id': self.user_id,
                    'timestamp': datetime.now().isoformat()
                }))

            else:
                raise ValueError("Missing authentication token")

        except Exception as e:
            logger.error(f"Authentication error: {str(e)}")
            await self.send_error(str(e), code=401)

    async def authenticate_user(self, user_token):
        self.user = await self.get_user_profile(user_token)
        if not self.user:
            raise ObjectDoesNotExist("User not found")
        self.user_id = str(self.user.user_id)

        async with self.users_lock:
            self.active_users.setdefault(self.user_id, set()).add(self.channel_name)

        await self.update_user_status(self.user_id, True)
        await self.join_presence_groups()

    async def authenticate_partner(self, partner_token):
        self.partner = await self.get_partner_profile(partner_token)
        if not self.partner:
            raise ObjectDoesNotExist("Partner not found")
        self.partner_id = str(self.partner.partner_id)

        async with self.partners_lock:
            self.active_partners.setdefault(self.partner_id, set()).add(self.channel_name)

        await self.update_partner_status(self.partner_id, True)
        await self.join_presence_groups()

    async def join_presence_groups(self):
        if self.user_id:
            await self.join_group(f"presence_user_{self.user_id}")
            if self.partner_id:
                await self.join_group(f"presence_partner_{self.partner_id}")

        if self.partner_id:
            await self.join_group(f"presence_partner_{self.partner_id}")
            if self.user_id:
                await self.join_group(f"presence_user_{self.user_id}")

    async def join_presence_group(self, data):
        group_name = data['group_name']
        await self.join_group(group_name)
        await self.send(json.dumps({
            'status': 'group_joined',
            'group': group_name,
            'timestamp': datetime.now().isoformat()
        }))
    # Message Handling
    async def handle_send_message(self, data):
        sender_type, user, partner = await self.validate_message_request(data)
        message = await self.create_message(user, partner, sender_type, data['message'])
        serialized_message = await self.serialize_message(message)

        room_name = f"chat_{user.user_id}_{partner.partner_id}"
        await self.join_group(room_name)

        await self.channel_layer.group_send(
            room_name,
            {
                "type": "chat_message",
                "message": serialized_message,
                "tempId": data.get("tempId")
            }
        )

        await self.update_inboxes(user, partner, serialized_message)

    async def chat_message(self, event):
        await self.send(json_serializer(event['message']))

    # Message Status Handlers
    async def handle_message_delivered(self, data):
        message_ids = data.get('message_ids', [])
        updated_ids = await self.mark_messages_delivered(message_ids)
        await self.notify_message_status(updated_ids, 'delivered')

    async def handle_message_read(self, data):
        message_ids = data.get('message_ids', [])
        updated_ids = await self.mark_messages_read(message_ids)
        await self.notify_message_status(updated_ids, 'read')

    async def handle_message_seen(self, data):
        user, partner = await self.validate_conversation_access(data)
        message_ids = await self.mark_conversation_seen(user, partner)
        await self.notify_bulk_seen_status(user, partner, message_ids)

    async def notify_message_status(self, message_ids: List[str], status: str):
        messages = await self.get_messages_by_ids(message_ids)
        payload = {
            'action': f'message_{status}',
            'message_ids': message_ids,
            'messages': [self.serialize_status_message(msg) for msg in messages],
            'timestamp': timezone.now().isoformat()
        }

        for msg in messages:
            sender_group = f"presence_{msg.user.user_id}" if msg.sender == 'user' else f"presence_{msg.partner.partner_id}"
            await self.channel_layer.group_send(sender_group, {
                "type": "status_update",
                "payload": payload
            })

    async def status_update(self, event):
        await self.send(json_serializer(event['payload']))

    # Inbox Handling
    async def handle_get_inbox(self, data):
        if self.user:
            messages = await self.get_user_inbox(self.user)
        elif self.partner:
            messages = await self.get_partner_inbox(self.partner)
        else:
            raise PermissionError("Not authenticated")

        await self.send(json_serializer({
            'action': 'inbox',
            'data': messages,
            'timestamp': datetime.now().isoformat()
        }))

    async def update_inboxes(self, user, partner, message):
        user_inbox = await self.get_user_inbox(user)
        partner_inbox = await self.get_partner_inbox(partner)

        await self.channel_layer.group_send(
            f"user_{user.user_id}",
            {"type": "inbox_update", "data": user_inbox}
        )
        await self.channel_layer.group_send(
            f"partner_{partner.partner_id}",
            {"type": "inbox_update", "data": partner_inbox}
        )

    async def inbox_update(self, event):
        await self.send(json_serializer(event))

    # Message History
    async def handle_get_messages(self, data):
        try:
            page = int(data.get('page', 1))
            page_size = min(int(data.get('page_size', 50)), 100)
            if not self.user and not self.partner:
                raise PermissionError("Not authenticated")
            user, partner = await self.validate_conversation_access(data)

            messages, total_pages = await self.get_paginated_messages(user, partner, page, page_size)

            await self.send(json_serializer({
                'action': 'messages',
                'data': messages,
                'meta': {'page': page, 'page_size': page_size, 'total_pages': total_pages},
                'timestamp': datetime.now().isoformat()
            }))
            await self.mark_messages_as_read(user, partner)
        except PermissionError as e:
            await self.send_error("Unauthorized access", code=403)
        except Exception as e:
            await self.send_error(str(e), code=500)

    # Typing Indicators
    async def handle_typing(self, data):
        user, partner = await self.validate_conversation_access(data)
        await self.channel_layer.group_send(
            f"chat_{user.user_id}_{partner.partner_id}",
            {
                "type": "typing_indicator",
                "is_typing": data.get('is_typing', False),
                "sender_type": 'user' if self.user else 'partner'
            }
        )

    async def typing_indicator(self, event):
        await self.send(json_serializer({
            'action': 'typing',
            'is_typing': event['is_typing'],
            'sender_type': event['sender_type'],
            'timestamp': datetime.now().isoformat()
        }))

    # Database Operations
    @database_sync_to_async
    def get_user_profile(self, session_token):
        return UserProfile.objects.filter(session_token=session_token).first()

    @database_sync_to_async
    def get_partner_profile(self, session_token):
        return PartnerProfile.objects.filter(partner_session_token=session_token).first()

    @database_sync_to_async
    def create_message(self, user, partner, sender, message):
        return ChatMessage.objects.create(
            user=user,
            partner=partner,
            sender=sender,
            message=message,
            is_delivered=False,
            is_read=False
        )

    @database_sync_to_async
    def get_paginated_messages(self, user, partner, page, page_size):
        qs = ChatMessage.objects.filter(
            user=user, partner=partner
        ).order_by('-date')
        paginator = Paginator(qs, page_size)
        return (
            [self.serialize_message_db(msg) for msg in paginator.page(page).object_list],
            paginator.num_pages
        )

    @database_sync_to_async
    def mark_messages_delivered(self, message_ids):
        return list(ChatMessage.objects.filter(
            id__in=message_ids,
            **self.get_recipient_filter()
        ).update(
            is_delivered=True,
            delivered_at=timezone.now()
        ))

    @database_sync_to_async
    def mark_messages_read(self, message_ids):
        return list(ChatMessage.objects.filter(
            id__in=message_ids,
            **self.get_recipient_filter()
        ).update(
            is_read=True,
            read_at=timezone.now()
        ))

    @database_sync_to_async
    def mark_conversation_seen(self, user, partner):
        return list(ChatMessage.objects.filter(
            user=user,
            partner=partner,
            sender='partner' if self.user else 'user',
            is_delivered=False
        ).update(
            is_delivered=True,
            delivered_at=timezone.now()
        ))

    async def handle_mark_read(self, data):
        user, partner = await self.validate_conversation_access(data)
        await self.mark_messages_as_read(user, partner)
        await self.send(json_serializer({
            'status': 'read_confirmed',
            'timestamp': datetime.now().isoformat()
        }))

    @database_sync_to_async
    def mark_messages_as_read(self, user, partner):
        ChatMessage.objects.filter(
            user=user,
            partner=partner,
            sender='partner' if self.user else 'user',
            is_read=False
        ).update(is_read=True)

    # Helper Methods
    def get_recipient_filter(self):
        return {'user': self.user, 'sender': 'partner'} if self.user else {'partner': self.partner, 'sender': 'user'}

    async def validate_message_request(self, data):
        if self.user:
            required_fields = ['partner_id', 'message']
        else:
            required_fields = ['user_id', 'message']

        for field in required_fields:
            if not data.get(field):
                raise ValueError(f"Missing {field}")

        if self.user:
            partner = await self.get_partner_by_id(data['partner_id'])
        else:
            user = await self.get_user_by_id(data['user_id'])

        return ('user' if self.user else 'partner'), (self.user or user), (self.partner or partner)

    async def validate_conversation_access(self, data):
        if self.user:
            partner = await self.get_partner_by_id(data['partner_id'])
            if not partner or str(self.user.user_id) != data.get('user_id'):
                raise PermissionError("Unauthorized access")
            return self.user, partner

        if self.partner:
            user = await self.get_user_by_id(data['user_id'])
            if not user or str(self.partner.partner_id) != data.get('partner_id'):
                raise PermissionError("Unauthorized access")
            return user, self.partner

        raise PermissionError("Authentication required")

    async def cleanup_presence(self):
        if self.user_id:
            async with self.users_lock:
                channels = self.active_users.get(self.user_id, set())
                channels.discard(self.channel_name)
                if not channels:
                    del self.active_users[self.user_id]
                    await self.set_user_online_status(self.user_id, False)

        if self.partner_id:
            async with self.partners_lock:
                channels = self.active_partners.get(self.partner_id, set())
                channels.discard(self.channel_name)
                if not channels:
                    del self.active_partners[self.partner_id]
                    await self.set_partner_online_status(self.partner_id, False)

    async def leave_all_groups(self):
        for group in self.active_groups:
            await self.channel_layer.group_discard(group, self.channel_name)
        self.active_groups.clear()

    async def join_group(self, group_name: str):
        await self.channel_layer.group_add(group_name, self.channel_name)
        self.active_groups.add(group_name)

    # Serialization
    def serialize_message_db(self, msg):
        return {
            'id': str(msg.id),
            'sender': msg.sender,
            'message': msg.message,
            'timestamp': msg.date.isoformat(),
            'is_read': msg.is_read,
            'is_delivered': msg.is_delivered,
            'read_at': msg.read_at.isoformat() if msg.read_at else None,
            'delivered_at': msg.delivered_at.isoformat() if msg.delivered_at else None,
            'user_id': str(msg.user.user_id),
            'partner_id': str(msg.partner.partner_id)
        }

    async def serialize_message(self, message):
        return await database_sync_to_async(self.serialize_message_db)(message)

    def serialize_status_message(self, msg):
        return {
            'id': str(msg.id),
            'sender': msg.sender,
            'user_id': str(msg.user.user_id),
            'partner_id': str(msg.partner.partner_id),
            'status_timestamp': msg.delivered_at.isoformat() if msg.is_delivered else msg.read_at.isoformat()
        }

    @database_sync_to_async
    def get_user_inbox(self, user):
        unread_subquery = ChatMessage.objects.filter(
            user=user,
            partner=OuterRef('partner'),
            sender='partner',
            is_read=False
        ).values('partner').annotate(count=Count('*')).values('count')

        messages = ChatMessage.objects.filter(user=user).annotate(
            rank=Window(
                expression=Rank(),
                partition_by=[F('partner')],
                order_by=F('date').desc()
            ),
            unread_count=Subquery(unread_subquery, output_field=IntegerField())
        ).filter(rank=1).select_related('partner')

        inbox_data = []
        for msg in messages:
            company_details = get_company_detail(msg)

            inbox_data.append({
                'id': str(msg.id),
                'partner_id': str(msg.partner.partner_id),
                'partner_name': msg.partner.name,
                'last_message': msg.message,
                'timestamp': msg.date.isoformat(),
                'is_delivered': msg.is_delivered,
                'is_read': msg.is_read,
                'unread_count': msg.unread_count or 0,
                'company_name': company_details.get('company_name') if company_details else None,
                'company_logo': company_details.get('company_logo') if company_details else None
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
            user_photo_url = msg.user.user_photo if msg.user.user_photo else None  # Convert to URL

            inbox_data.append({
                'id': str(msg.id),
                'user_id': str(msg.user.user_id),  # user_id
                'user_fullname': msg.user.name,  # user_fullname
                'user_photo': user_photo_url,  # user_photo as URL
                'user_name': msg.user.name,  # user_name if needed as well
                'last_message': msg.message,
                'timestamp': msg.date.isoformat(),
                'is_delivered': msg.is_delivered,
                'is_read': msg.is_read,
                'unread_count': ChatMessage.objects.filter(
                    partner=partner,
                    user=msg.user,
                    sender='user',
                    is_read=False
                ).count()
            })

        return inbox_data

    def serialize_inbox_item(self, msg, sender_type):
        base = {
            'id': str(msg.id),
            'last_message': msg.message,
            'timestamp': msg.date.isoformat(),
            'unread_count': msg.unread_count,
            'is_delivered': msg.is_delivered,
            'is_read': msg.is_read
        }

        if sender_type == 'partner':
            company = get_company_detail(msg)
            base.update({
                'partner_id': str(msg.partner.partner_id),
                'name': msg.partner.name,
                'company_name': company.get('name'),
                'logo': company.get('logo')
            })
        else:
            base.update({
                'user_id': str(msg.user.user_id),
                'name': msg.user.name,
                'avatar': msg.user.user_photo if msg.user.user_photo else None
            })
        return base

    # Presence Updates
    async def update_user_status(self, user_id: str, online: bool):
        await self.set_user_online_status(user_id, online)
        await self.broadcast_presence(f"presence_{user_id}", 'user', user_id, online)

    async def update_partner_status(self, partner_id: str, online: bool):
        await self.set_partner_online_status(partner_id, online)
        await self.broadcast_presence(f"presence_{partner_id}", 'partner', partner_id, online)

    async def broadcast_presence(self, group: str, entity_type: str, entity_id: str, online: bool):
        await self.channel_layer.group_send(
            group,
            {
                "type": "presence.update",  # Matches handler method name
                "entity_type": entity_type,
                "entity_id": entity_id,
                "status": "online" if online else "offline",
                "timestamp": datetime.now().isoformat()
            }
        )

    async def presence_update(self, event):
        """Send presence updates to connected clients"""
        print(f"Sending presence update: {event}")
        await self.send(text_data=json.dumps({
            "action": "presence_update",
            "entity_type": event["entity_type"],
            "entity_id": event["entity_id"],
            "status": event["status"],
            "timestamp": event["timestamp"]
        }))

    @database_sync_to_async
    def set_user_online_status(self, user_id: str, online: bool):
        UserProfile.objects.filter(user_id=user_id).update(
            online=online,
            last_seen=timezone.now() if not online else None
        )

    @database_sync_to_async
    def set_partner_online_status(self, partner_id: str, online: bool):
        PartnerProfile.objects.filter(partner_id=partner_id).update(
            online=online,
            last_seen=timezone.now() if not online else None
        )

    # Utility Methods
    @database_sync_to_async
    def get_user_by_id(self, user_id: str):
        return UserProfile.objects.filter(user_id=user_id).first()

    @database_sync_to_async
    def get_partner_by_id(self, partner_id: str):
        return PartnerProfile.objects.filter(partner_id=partner_id).first()

    @database_sync_to_async
    def get_messages_by_ids(self, message_ids):
        return list(ChatMessage.objects.filter(id__in=message_ids).select_related('user', 'partner'))

    def get_rate_limit_key(self):
        return self.user_id or self.partner_id or self.channel_name

    async def send_error(self, message: str, code: int = 400):
        await self.send(json_serializer({
            'status': 'error',
            'code': code,
            'message': message,
            'timestamp': datetime.now().isoformat()
        }))