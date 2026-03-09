from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0003_payment_review_message"),
    ]

    operations = [
        migrations.AddConstraint(
            model_name="payment",
            constraint=models.UniqueConstraint(
                condition=models.Q(transaction_number__isnull=False)
                & ~models.Q(transaction_number=""),
                fields=("transaction_number",),
                name="payment_transaction_number_unique",
            ),
        ),
    ]
