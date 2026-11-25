from dataclasses import dataclass

from django.core.exceptions import PermissionDenied, ValidationError
from django.db import transaction

from accounts.security import audit

from .models import (
    Attempt,
    ExamSession,
    Question,
    Quiz,
    QuizDeletionAuthorization,
    QuizEnrollment,
    ScoreRevision,
)


@dataclass(frozen=True)
class DeletedQuiz:
    quiz_id: int
    title: str


def can_delete_quiz(actor, quiz):
    if actor.role == 'admin':
        return True
    return (
        actor.role == 'teacher'
        and quiz.teacher_id == actor.pk
        and quiz.class_room.teacher_id == actor.pk
    )


@transaction.atomic
def delete_quiz(*, actor, quiz_id, reason):
    """Permanently delete one quiz and its exclusive records atomically."""
    reason = (reason or '').strip()
    if not reason:
        raise ValidationError('A deletion reason is required.')
    if len(reason) > 500:
        raise ValidationError('The deletion reason must be 500 characters or fewer.')

    quiz = (
        Quiz.objects.select_for_update()
        .select_related('class_room')
        .get(pk=quiz_id)
    )
    if not can_delete_quiz(actor, quiz):
        raise PermissionDenied('You are not authorized to delete this quiz.')

    # Lock in the same parent-to-child order used by exam operations.  Locking
    # the quiz first serializes deletion against opening/starting a session.
    replacements = list(
        Quiz.objects.select_for_update()
        .filter(replaces_id=quiz.pk)
        .order_by('pk')
    )
    sessions = list(
        ExamSession.objects.select_for_update()
        .filter(quiz_id=quiz.pk)
        .order_by('pk')
    )
    if any(session.status == 'open' for session in sessions):
        raise ValidationError('Close the open exam session before deleting this quiz.')

    attempts = list(
        Attempt.objects.select_for_update()
        .filter(quiz_id=quiz.pk)
        .order_by('pk')
    )
    list(Question.objects.select_for_update().filter(quiz_id=quiz.pk).order_by('pk'))
    list(QuizEnrollment.objects.select_for_update().filter(quiz_id=quiz.pk).order_by('pk'))

    attempt_ids = [attempt.pk for attempt in attempts]
    if attempt_ids:
        list(
            ScoreRevision.objects.select_for_update()
            .filter(attempt_id__in=attempt_ids)
            .order_by('pk')
        )

    # Database triggers keep published assessment and grading data immutable.
    # A short-lived authorization row allows only this checked transaction to
    # remove records belonging to the selected quiz.
    authorized_ids = [quiz.pk, *(replacement.pk for replacement in replacements)]
    QuizDeletionAuthorization.objects.bulk_create(
        [QuizDeletionAuthorization(quiz_id=value) for value in authorized_ids],
        ignore_conflicts=False,
    )

    if replacements:
        Quiz.objects.filter(pk__in=[replacement.pk for replacement in replacements]).update(replaces=None)
    if attempt_ids:
        ScoreRevision.objects.filter(attempt_id__in=attempt_ids).delete()

    deleted = DeletedQuiz(quiz_id=quiz.pk, title=quiz.title)
    quiz.delete()
    audit(actor, 'quiz.deleted', quiz_id=deleted.quiz_id, reason=reason)
    QuizDeletionAuthorization.objects.filter(quiz_id__in=authorized_ids).delete()
    return deleted
