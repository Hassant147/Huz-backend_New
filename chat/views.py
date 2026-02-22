from rest_framework import status
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from .models import ChatMessage
from .serializer import MessageSerializer
from rest_framework import generics
from common.models import UserProfile
from partners.models import PartnerProfile
from common.logs_file import logger
from rest_framework.exceptions import NotFound
from django.db.models import OuterRef, Subquery
from rest_framework.views import APIView
from drf_yasg.utils import swagger_auto_schema
from drf_yasg import openapi


class SendMessages(generics.CreateAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = MessageSerializer

    def create(self, request, *args, **kwargs):
        try:
            user_session_token = request.data.get('user_session_token')
            partner_session_token = request.data.get('partner_session_token')
            sender = request.data.get('sender')
            message = request.data.get('message')

            # Validate that all required fields are provided
            if not user_session_token or not partner_session_token or not message:
                return Response({"error": "Missing required fields."}, status=status.HTTP_400_BAD_REQUEST)

            # Get the user and partner objects
            user_profile = UserProfile.objects.filter(session_token=user_session_token).first()
            if not user_profile:
                logger.error(f"UserProfile with session_token {user_session_token} not found.")
                return Response({"detail":"User not found."}, status=status.HTTP_400_BAD_REQUEST)

            partner_profile = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not partner_profile:
                logger.error(f"PartnerProfile with id {partner_session_token} not found.")
                return Response({"detail":"Partner not found."}, status=status.HTTP_400_BAD_REQUEST)

            # Create and save the new ChatMessage
            chat_message = ChatMessage.objects.create(
                user=user_profile,
                partner=partner_profile,
                sender=sender,  # Assuming the sender is the user
                message=message,
                is_read=False
            )
            serializer = self.get_serializer(chat_message)
            return Response(serializer.data, status=status.HTTP_201_CREATED)

        except Exception as e:
            logger.error(f"Error in SendMessages view: {str(e)}")
            return Response({"error": "An error occurred while sending the message."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class UserInbox(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = MessageSerializer  # Specify the serializer to use

    def get_queryset(self):
        try:
            user_session_token = self.kwargs['user_session_token']
            user_profile = UserProfile.objects.filter(session_token=user_session_token).first()
            if not user_profile:
                logger.error(f"UserProfile with session_token {user_session_token} not found.")
                raise NotFound(detail="User not found")
            last_messages_subquery = ChatMessage.objects.filter(
                user=user_profile,
                partner=OuterRef('partner')  # Match messages with the same partner
            ).order_by('-date')  # Order by date to get the latest message

            latest_messages = ChatMessage.objects.filter(
                id=Subquery(last_messages_subquery.values('id')[:1])  # Get only the latest message per partner
            )
            return latest_messages

        except Exception as e:
            logger.error(f"Unexpected error occurred in UserInbox view: {str(e)}")
            raise


class PartnerInbox(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = MessageSerializer  # Specify the serializer to use

    def get_queryset(self):
        try:
            partner_session_token = self.kwargs['partner_session_token']
            partnerProfile = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not partnerProfile:
                logger.error(f"Partner Profile with session_token {partner_session_token} not found.")
                raise NotFound(detail="User not found")
            last_messages_subquery = ChatMessage.objects.filter(
                partner=partnerProfile,
                user=OuterRef('user')
            ).order_by('-date')  # Order by date to get the latest message

            latest_messages = ChatMessage.objects.filter(
                id=Subquery(last_messages_subquery.values('id')[:1])  # Get only the latest message per partner
            )
            return latest_messages

        except Exception as e:
            logger.error(f"Unexpected error occurred in UserInbox view: {str(e)}")
            raise


class GetMessages(generics.ListAPIView):
    permission_classes = [IsAdminUser]
    serializer_class = MessageSerializer

    def get_queryset(self):
        try:
            # Get the user session token and partner ID from the URL parameters or query parameters
            user_session_token = self.kwargs['user_session_token']
            partner_session_token = self.kwargs['partner_session_token']
            # Ensure user profile exists
            user_profile = UserProfile.objects.filter(session_token=user_session_token).first()
            if not user_profile:
                logger.error(f"UserProfile with session_token {user_session_token} not found.")
                raise NotFound(detail="User not found")

            # Ensure partner profile exists
            partner_profile = PartnerProfile.objects.filter(partner_session_token=partner_session_token).first()
            if not partner_profile:
                logger.error(f"PartnerProfile with id {partner_session_token} not found.")
                raise NotFound(detail="Partner not found")

            # Fetch all the messages between the user and the partner
            messages = ChatMessage.objects.filter(
                user=user_profile,
                partner=partner_profile
            ).order_by('date')  # Order by date to get the correct order of messages

            return messages

        except Exception as e:
            logger.error(f"Unexpected error occurred in GetMessages view: {str(e)}")
            raise


class DeleteAllMessages(APIView):
    permission_classes = [IsAdminUser]

    @swagger_auto_schema(
        operation_description="Delete all chat messages.",
        responses={
            200: openapi.Response('All messages deleted successfully'),
            500: openapi.Response('An error occurred while deleting messages'),
        }
    )
    def delete(self, request, *args, **kwargs):
        try:
            # Delete all messages in the database
            messages_deleted, _ = ChatMessage.objects.all().delete()

            if messages_deleted:
                return Response({"detail": "All messages deleted successfully."}, status=status.HTTP_200_OK)
            else:
                return Response({"detail": "No messages found to delete."}, status=status.HTTP_404_NOT_FOUND)

        except Exception as e:
            return Response({"error": f"An error occurred: {str(e)}"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
