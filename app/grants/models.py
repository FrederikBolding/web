from django.db import models

from economy.models import SuperModel


class Grant(SuperModel):
    """Define the structure of a Grant."""

    title = models.CharField(max_length=255)
    pitch = models.CharField(max_length=255, default='')
    description = models.TextField(default='', blank=True)
    reference_url = models.URLField(db_index=True)
    current_funding = models.DecimalField(default=0, decimal_places=4, max_digits=50)
    goal_funding = models.DecimalField(default=0, decimal_places=4, max_digits=50)

    profile = models.ForeignKey('dashboard.Profile', related_name='grants', on_delete=models.CASCADE, null=True)

    def percentage_done(self):
        return self.current_funding / self.goal_funding * 100


class Stakeholder(models.Model):
    """Define relationship for profiles expressing interest on a bounty."""

    eth_address = models.CharField(max_length=50)
    name = models.CharField(max_length=255, blank=True)
    role = models.CharField(max_length=255, blank=True)
    url = models.URLField(db_index=True)

    def __str__(self):
        return self.name