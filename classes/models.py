from django.conf import settings
from django.db import models


class ClassRoom(models.Model):
    name = models.CharField(max_length=200)
    teacher = models.ForeignKey(settings.AUTH_USER_MODEL, related_name='taught_classes', on_delete=models.CASCADE)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ClassMembership(models.Model):
    class_room = models.ForeignKey(ClassRoom, related_name='memberships', on_delete=models.CASCADE)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, limit_choices_to={'role': 'student'}, related_name='class_memberships', on_delete=models.CASCADE)
    joined_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('class_room', 'student')

    def __str__(self):
        return f'{self.student.username} in {self.class_room.name}'
