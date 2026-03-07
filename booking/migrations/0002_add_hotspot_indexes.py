from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0001_initial"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["booking_status", "order_time"],
                name="booking_status_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["order_by", "booking_status"],
                name="booking_user_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["order_to", "booking_status"],
                name="booking_partner_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["package_token", "booking_status"],
                name="booking_package_status_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["start_date"],
                name="booking_start_date_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="payment",
            index=models.Index(
                fields=["booking_token", "transaction_time"],
                name="payment_booking_time_idx",
            ),
        ),
    ]
