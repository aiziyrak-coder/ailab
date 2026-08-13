from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0004_analysis_record"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrecord",
            name="patient_name",
            field=models.CharField(blank=True, default="", max_length=120),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="sample_id",
            field=models.CharField(blank=True, db_index=True, default="", max_length=40),
        ),
    ]
