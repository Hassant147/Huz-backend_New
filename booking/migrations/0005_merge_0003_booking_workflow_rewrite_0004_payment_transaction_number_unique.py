from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0003_booking_workflow_rewrite"),
        ("booking", "0004_payment_transaction_number_unique"),
    ]

    operations = []
