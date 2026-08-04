# The `sheet` file-type choice, arriving with toto.primula (delta 1.10).

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('vault', '0009_alter_vaultfile_file_type'),
    ]

    operations = [
        migrations.AlterField(
            model_name='vaultfile',
            name='file_type',
            field=models.CharField(choices=[('pdf', 'PDF'), ('image', 'Image'), ('html', 'HTML'), ('text', 'Text File'), ('json', 'JSON'), ('yaml', 'YAML'), ('xml', 'XML'), ('latex', 'LaTeX'), ('bib', 'Bibliography'), ('csv', 'CSV'), ('svg', 'SVG File'), ('audio', 'Audio'), ('video', 'Video'), ('python', 'Python'), ('neojson', 'NeoJSON'), ('presentation', 'Presentation'), ('document', 'Document'), ('sheet', 'Primula Sheet'), ('zip', 'Archive')], max_length=16),
        ),
    ]
