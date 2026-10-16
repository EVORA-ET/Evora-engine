from django.core.management.base import BaseCommand

from ev_migration.catalogue.loader import SEED, load
from ev_migration.models import EVModel


class Command(BaseCommand):
    help = ('Load or refresh EV catalogue entries from a cited JSON file '
            '(default: the bundled seed). Changed entries become unverified.')

    def add_arguments(self, parser):
        parser.add_argument('path', nargs='?', default=str(SEED))

    def handle(self, *args, path, **options):
        created, updated = load(EVModel, path)
        self.stdout.write(f'{created} created, {updated} updated.')
