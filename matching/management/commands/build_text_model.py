"""Compile matching/lexicon.py into a versioned text-model release.

    python manage.py build_text_model --release lexical-v1          # write / verify
    python manage.py build_text_model --release lexical-v1 --check  # verify only

A release is immutable: if ``matching/text_model/<release>.json`` already
exists with different content, the command refuses to overwrite it. Changing
the lexicon therefore requires a *new* release name, selecting it in
``settings.MATCHING['TEXT_MODEL_RELEASE']``, bumping the engine version in
``matching/profile.json`` and running ``rebuild_matches``.
"""
from django.core.management.base import BaseCommand, CommandError

from matching import textmodel


class Command(BaseCommand):
    help = 'Build (or verify) a fixed-vocabulary text-model release from matching/lexicon.py.'

    def add_arguments(self, parser):
        parser.add_argument('--release', default=textmodel.DEFAULT_RELEASE)
        parser.add_argument('--check', action='store_true',
                            help='Only verify that the committed release matches the lexicon.')

    def handle(self, release, check=False, **options):
        try:
            path = textmodel.release_path(release)
        except ValueError as exc:
            raise CommandError(str(exc)) from exc
        content = textmodel.release_json(textmodel.build_release(release))
        if path.exists():
            existing = path.read_text(encoding='utf-8')
            if existing == content:
                self.stdout.write(self.style.SUCCESS(f'{path.name} is up to date.'))
                return
            raise CommandError(
                f'{path.name} exists and differs from the current lexicon. Releases are immutable: '
                'build a new release name, select it in settings, bump the engine version and '
                'run rebuild_matches.')
        if check:
            raise CommandError(f'{path.name} does not exist.')
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content.encode('utf-8'))  # LF on every platform
        self.stdout.write(self.style.SUCCESS(f'Wrote {path} ({len(content)} bytes).'))
