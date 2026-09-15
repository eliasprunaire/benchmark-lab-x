"""Transport OpenRouter unique pour la qualification automatisée S17"""
from pathlib import Path

from .openrouter_preparation import OpenRouterPreparation, configuration, load_profile
from .storage import _fields, _text


PROFILE = Path(__file__).with_name('qualification.profile.json')
ASSISTANT = 'qualification'
KINDS = {'coherence', 'fiction', 'decidability', 'leak'}
SEVERITIES = {'blocking', 'note'}


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

    def validate_answer(self, result, message):
        _fields(result, ('qualified', 'findings', 'summary'), 'qualification automatisée')
        if type(result['qualified']) is not bool or type(result['findings']) is not list:
            raise ValueError('Qualification automatisée invalide')
        _text(result['summary'], 'summary')
        if not result['summary'].strip():
            raise ValueError('Résumé de qualification requis')
        for finding in result['findings']:
            _fields(finding, ('kind', 'severity', 'text'), 'constat de qualification')
            if finding['kind'] not in KINDS or finding['severity'] not in SEVERITIES:
                raise ValueError('Constat de qualification invalide')
            _text(finding['text'], 'text')
            if not finding['text'].strip():
                raise ValueError('Texte de constat requis')
        if result['qualified'] != all(row['severity'] != 'blocking' for row in result['findings']):
            raise ValueError('Verdict de qualification divergent')
        return result
