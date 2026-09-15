"""Transport OpenRouter unique pour la qualification automatisée S17"""
from pathlib import Path

from .openrouter_preparation import OpenRouterPreparation, configuration, load_profile


PROFILE = Path(__file__).with_name('qualification.profile.json')
ASSISTANT = 'qualification'


class OpenRouterQualification(OpenRouterPreparation):
    phases = ('qualification',)

    def __init__(self, api_key, profile=None):
        if profile in (None, ASSISTANT):
            profile = load_profile(str(PROFILE))
        super().__init__(api_key, profile)

    def configuration(self):
        return configuration(profile=self._profile)

    def content(self, request):
        return request['outgoing']
