from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0006_alter_booking_status_changed_at_and_more"),
    ]

    operations = [
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["order_by", "order_time"],
                name="booking_user_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["order_to", "order_time"],
                name="booking_partner_time_idx",
            ),
        ),
        migrations.AddIndex(
            model_name="booking",
            index=models.Index(
                fields=["order_to", "booking_number"],
                name="booking_partner_number_idx",
            ),
        ),
    ]
