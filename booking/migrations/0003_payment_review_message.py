from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("booking", "0002_add_hotspot_indexes"),
    ]

    operations = [
        migrations.AddField(
            model_name="payment",
            name="review_message",
            field=models.TextField(blank=True, null=True),
        ),
    ]
