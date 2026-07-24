# Recommendation tuning singleton: RecommendationConfig.

import django.core.validators
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('academy', '0002_personalpath_personalpathstep'),
    ]

    operations = [
        migrations.CreateModel(
            name='RecommendationConfig',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('cf_strength', models.FloatField(default=0.4, help_text='Maximum collaborative-filtering share of the hybrid score (0 = pure content-based).', validators=[django.core.validators.MinValueValidator(0.0), django.core.validators.MaxValueValidator(1.0)])),
                ('cold_start_students', models.PositiveIntegerField(default=20, help_text='Students with at least two badges needed for the collaborative share to reach full strength.')),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Recommendation configuration',
            },
        ),
    ]
