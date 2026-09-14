"""Explicit judgment profile over the S13 single-exchange transport."""
from . import outgoing
from .openrouter_preparation import OpenRouterPreparation
from .storage import _fields


class OpenRouterJudgment(OpenRouterPreparation):
    phases = ('judgment',)

    def __init__(self, api_key, profile):
        super().__init__(api_key, profile)

    def content(self, request):
        return outgoing.closed_review(request['outgoing'])

    def validate_document(self, document):
        authorized = {row['provider_name'] for row in self._profile['routes']}
        metadata = document.get('openrouter_metadata')
        rows = [document]
        if type(metadata) is dict:
            attempts = metadata.get('attempts', [])
            endpoints = metadata.get('endpoints', {})
            available = endpoints.get('available', []) if type(endpoints) is dict else []
            if type(attempts) is list:
                rows += attempts
            if type(available) is list:
                rows += [row for row in available if type(row) is dict and row.get('selected') is True]
        if any(type(row) is dict and row.get('provider') is not None
               and row['provider'] not in authorized for row in rows):
            raise ValueError('Fournisseur rapporté hors profil de jugement')

    def validate_answer(self, result, message):
        _fields(result, ('findings', 'measures', 'limits', 'proposed_verdict'), 'judgment proposal')
        if (message.get('refusal') or message.get('function_call')
                or result['proposed_verdict'] not in ('SATISFAIT', 'NE SATISFAIT PAS', 'INDETERMINE')):
            raise ValueError('Proposition inexploitable')
        return result
