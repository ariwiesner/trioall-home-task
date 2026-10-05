"""
Wires reprocess_unresolved_readings() into the registry's normal
configuration flow (Django admin — see fridges/admin.py's LoggerAdmin,
which inlines LoggerAssignment so both can be set in one form) so that
adding/fixing a logger "just works" with no manual step.

Lives in `readings`, not `fridges`, because `readings` already depends on
`fridges` (ingestion.py imports its models) — `fridges` itself stays
unaware that `readings` exists, which is the direction the rest of the
codebase already uses.

Fires on every save, not just creation: editing an existing Logger's unit
or a LoggerAssignment's start_at to fix a mistake should also unstick
whatever it was blocking. reprocess_unresolved_readings() is cheap and
idempotent at this data scale (only ever touches rows that are still
is_valid=True and refrigerator=None), so firing on every save rather than
only on first-creation isn't worth special-casing.
"""

from django.db.models.signals import post_save
from django.dispatch import receiver

from fridges.models import Logger, LoggerAssignment

from .ingestion import reprocess_unresolved_readings


@receiver(post_save, sender=Logger)
def reprocess_on_logger_saved(sender, instance, **kwargs):
    reprocess_unresolved_readings(logger_external_id=instance.external_id)


@receiver(post_save, sender=LoggerAssignment)
def reprocess_on_assignment_saved(sender, instance, **kwargs):
    reprocess_unresolved_readings(logger_external_id=instance.logger.external_id)
