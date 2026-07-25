# Optional plain-text hint on QuizQuestion.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('quizzes', '0003_backfill_question_solutions'),
    ]

    operations = [
        migrations.AddField(
            model_name='quizquestion',
            name='hint',
            field=models.TextField(blank=True, help_text="Optional plain-text hint shown in a modal via the 'Show hint' button on the practice page."),
        ),
    ]
