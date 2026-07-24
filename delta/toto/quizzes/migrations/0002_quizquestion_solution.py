# Rich WYSIWYG worked solution on QuizQuestion.

from django.db import migrations
import trix_editor.fields


class Migration(migrations.Migration):

    dependencies = [
        ('quizzes', '0001_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='quizquestion',
            name='solution',
            field=trix_editor.fields.TrixEditorField(blank=True, help_text='Rich worked solution (WYSIWYG). Shown after any practice submission and in the graded review. Falls back to the plain explanation when empty.'),
        ),
    ]
