from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("api", "0005_analysis_patient_sample"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrecord",
            name="age",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="sex",
            field=models.CharField(blank=True, default="", max_length=16),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="ward",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="specimen_site",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="clinical_note",
            field=models.CharField(blank=True, default="", max_length=200),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="region",
            field=models.CharField(blank=True, default="", max_length=40),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="locality",
            field=models.CharField(blank=True, default="", max_length=80),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="clinic",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
        migrations.AddField(
            model_name="analysisrecord",
            name="facility_type",
            field=models.CharField(blank=True, default="", max_length=8),
        ),
    ]
