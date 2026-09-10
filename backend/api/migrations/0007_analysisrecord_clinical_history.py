from django.db import migrations, models


class Migration(migrations.Migration):
    """Shikoyat / anamnez / status localis — shifokorning erkin klinik yozuvi.

    200 belgilik «klinik izoh» bunga sig'maydi; patolog anamnez bilan
    ishlaydi (toshma tarqoqmi, davomiyligi, qichishish, avvalgi davolash).
    """

    dependencies = [
        ("api", "0006_analysis_patient_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="analysisrecord",
            name="clinical_history",
            field=models.TextField(blank=True, default=""),
        ),
    ]
