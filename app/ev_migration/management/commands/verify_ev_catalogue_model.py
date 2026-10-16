from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from ev_migration.models import EVModel


class Command(BaseCommand):
    help = ('Record that an EVORA staff member checked a catalogue entry '
            'against its cited document (or rejected it).')

    def add_arguments(self, parser):
        parser.add_argument('model_id')
        parser.add_argument('--by', required=True, help='Staff member name')
        parser.add_argument('--note', required=True)
        parser.add_argument('--reject', action='store_true')

    def handle(self, *args, model_id, by, note, reject, **options):
        m = EVModel.objects.filter(pk=model_id).first()
        if m is None:
            raise CommandError('No catalogue model with that id.')
        m.verification_status = 'rejected' if reject else 'verified'
        m.verified_by_name = by
        m.verified_at = timezone.now()
        m.verification_note = note
        m.save(update_fields=['verification_status', 'verified_by_name',
                              'verified_at', 'verification_note',
                              'updated_at'])
        self.stdout.write(f'{m.label()}: {m.verification_status}.')
