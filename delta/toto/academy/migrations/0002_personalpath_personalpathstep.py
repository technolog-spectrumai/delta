# Personalized learning paths: PersonalPath + PersonalPathStep.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('competence', '0001_initial'),
        ('academy', '0001_initial'),
    ]

    operations = [
        migrations.CreateModel(
            name='PersonalPath',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=200)),
                ('status', models.CharField(choices=[('active', 'Active'), ('completed', 'Completed'), ('archived', 'Archived')], default='active', max_length=12)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('goal_badge', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='targeted_by_personal_paths', to='competence.skillbadge')),
                ('goal_group', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='targeted_by_personal_paths', to='competence.skillgroup')),
                ('student', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='personal_paths', to='academy.student')),
            ],
            options={
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='PersonalPathStep',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('step_type', models.CharField(choices=[('module', 'Course module'), ('badge', 'Earn skill badge'), ('task', 'Task')], max_length=10)),
                ('title', models.CharField(blank=True, max_length=200)),
                ('note', models.TextField(blank=True)),
                ('order', models.PositiveIntegerField(default=0)),
                ('is_completed', models.BooleanField(default=False)),
                ('completed_at', models.DateTimeField(blank=True, null=True)),
                ('badge', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='personal_path_steps', to='competence.skillbadge')),
                ('module', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='personal_path_steps', to='academy.coursemodule')),
                ('path', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='steps', to='academy.personalpath')),
            ],
            options={
                'ordering': ['order', 'id'],
            },
        ),
        migrations.AddConstraint(
            model_name='personalpath',
            constraint=models.CheckConstraint(check=models.Q(models.Q(('goal_badge__isnull', False), ('goal_group__isnull', True)), models.Q(('goal_badge__isnull', True), ('goal_group__isnull', False)), _connector='OR'), name='personal_path_exactly_one_goal'),
        ),
        migrations.AddConstraint(
            model_name='personalpath',
            constraint=models.UniqueConstraint(condition=models.Q(('status', 'active')), fields=('student',), name='unique_active_personal_path_per_student'),
        ),
        migrations.AddConstraint(
            model_name='personalpathstep',
            constraint=models.UniqueConstraint(condition=models.Q(('badge__isnull', False)), fields=('path', 'badge'), name='unique_badge_step_per_personal_path'),
        ),
        migrations.AddConstraint(
            model_name='personalpathstep',
            constraint=models.CheckConstraint(check=models.Q(models.Q(('badge__isnull', True), ('step_type', 'task')), models.Q(('badge__isnull', False), ('step_type__in', ['module', 'badge'])), _connector='OR'), name='personal_path_step_type_badge_consistency'),
        ),
    ]
