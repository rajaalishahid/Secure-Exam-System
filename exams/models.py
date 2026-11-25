from datetime import timedelta

from django.conf import settings
from django.db import models
from django.utils import timezone

from classes.models import ClassRoom


class Quiz(models.Model):
    STATUS_CHOICES = [
        ('draft', 'Draft'),
        ('published', 'Published'),
    ]

    replaces = models.ForeignKey('self', null=True, blank=True, on_delete=models.PROTECT, related_name='replacement_quizzes')
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='quizzes')
    class_room = models.ForeignKey(ClassRoom, on_delete=models.CASCADE, related_name='quizzes')
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='draft')
    version = models.IntegerField(default=1)
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    published_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return self.title


class Question(models.Model):
    quiz = models.ForeignKey(Quiz, related_name='questions', on_delete=models.CASCADE)
    prompt = models.TextField()
    option_a = models.CharField(max_length=400)
    option_b = models.CharField(max_length=400)
    option_c = models.CharField(max_length=400)
    option_d = models.CharField(max_length=400)
    correct_option = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')])
    marks = models.IntegerField(default=1)

    def __str__(self):
        return self.prompt[:60]


class ExamSession(models.Model):
    STATUS_CHOICES = [
        ('scheduled', 'Scheduled'),
        ('open', 'Open'),
        ('closed', 'Closed'),
    ]

    quiz = models.ForeignKey(Quiz, related_name='sessions', on_delete=models.CASCADE)
    starts_at = models.DateTimeField(default=timezone.now)
    ends_at = models.DateTimeField()
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='scheduled')
    join_code = models.CharField(max_length=64, blank=True, default='')
    token_digest = models.CharField(max_length=64, blank=True)
    invitation_cipher = models.TextField(blank=True)
    closed_at = models.DateTimeField(null=True)
    closure_reason = models.TextField(blank=True)
    review_released = models.BooleanField(default=False)
    network_profile = models.ForeignKey('NetworkProfile', null=True, on_delete=models.PROTECT)
    network_policy = models.TextField(default='local-network')
    network_name = models.CharField(max_length=120, blank=True, default='')
    authorized_networks = models.TextField(blank=True, default='')

    def save(self, *args, **kwargs):
        if not self.ends_at:
            self.ends_at = self.starts_at + timedelta(minutes=15)
        super().save(*args, **kwargs)

    def __str__(self):
        return f'{self.quiz.title} - {self.status}'


class Attempt(models.Model):
    STATUS_CHOICES = [
        ('in_progress', 'In Progress'),
        ('submitted', 'Submitted'),
        ('expired', 'Expired'),
    ]

    student = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='attempts', on_delete=models.CASCADE)
    quiz = models.ForeignKey(Quiz, related_name='attempts', on_delete=models.CASCADE)
    session = models.ForeignKey(ExamSession, related_name='attempts', on_delete=models.CASCADE)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='in_progress')
    started_at = models.DateTimeField(auto_now_add=True)
    deadline_at = models.DateTimeField()
    submitted_at = models.DateTimeField(null=True, blank=True)
    score = models.FloatField(default=0)
    total_marks = models.PositiveIntegerField(default=0)
    quiz_version = models.PositiveIntegerField(default=1)
    question_order = models.JSONField(default=list)

    class Meta:
        unique_together = ('student', 'quiz')

    def __str__(self):
        return f'{self.student.username} -> {self.quiz.title}'


class Answer(models.Model):
    attempt = models.ForeignKey(Attempt, related_name='answers', on_delete=models.CASCADE)
    question = models.ForeignKey(Question, related_name='answers', on_delete=models.CASCADE)
    selected_option = models.CharField(max_length=1, choices=[('A', 'A'), ('B', 'B'), ('C', 'C'), ('D', 'D')], blank=True)
    saved_at = models.DateTimeField(auto_now=True)
    version = models.IntegerField(default=0)

    class Meta:
        unique_together = ('attempt', 'question')

    def __str__(self):
        return f'{self.attempt} / Q{self.question_id}'

class QuizEnrollment(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name='enrollments')
    student = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT)
    class Meta:
        unique_together = ('quiz','student')

class NetworkProfile(models.Model):
    name = models.CharField(max_length=120, unique=True)
    cidrs = models.TextField()
    interface = models.CharField(max_length=120, blank=True)
    enabled = models.BooleanField(default=True)
    def __str__(self): return self.name

class SaveReceipt(models.Model):
    attempt = models.ForeignKey(Attempt,on_delete=models.CASCADE)
    key = models.CharField(max_length=64)
    question_id_value = models.BigIntegerField()
    selected = models.CharField(max_length=1)
    version = models.PositiveIntegerField()
    class Meta:
        unique_together = ('attempt','key')

class ScoreRevision(models.Model):
    attempt = models.ForeignKey(Attempt, on_delete=models.PROTECT, related_name='revisions')
    previous = models.FloatField()
    corrected = models.FloatField()
    reason = models.TextField()
    approver = models.ForeignKey(settings.AUTH_USER_MODEL,on_delete=models.PROTECT)
    created_at = models.DateTimeField(auto_now_add=True)


class QuizDeletionAuthorization(models.Model):
    """Transaction-local authorization consumed by immutable-record triggers.

    Rows exist only inside an approved quiz-deletion transaction.  They are not
    a user-facing permission mechanism; application authorization is performed
    before a row is created.
    """

    quiz_id = models.PositiveBigIntegerField(unique=True)
    created_at = models.DateTimeField(auto_now_add=True)
