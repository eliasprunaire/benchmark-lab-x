"""Fixtures HTML du harnais Chromium existant, sans accès fournisseur"""
from benchmark_web import views


def example_view():
    return {'dossier_id': 'd1', 'revision': 1, 'current_revision': 1, 'stage': 'preview',
            'validation': None, 'qualified': False, 'explanation': 'Exemple fictif prêt à examiner',
            'payload': {'request': 'Comparer des comptes rendus fictifs', 'clarifications': [],
                        'validated_assumptions': [], 'reformulation': '', 'fictional_parameters': {}},
            'package': {'instruction': 'Rédiger le compte rendu', 'deliverables': ['Compte rendu'],
                        'pieces': [{'id': 'p1', 'name': 'reunion.txt'}], 'human_work': 'Relire', 'acceptable_ambiguities': [], 'limits': []},
            'example_contents': {'p1': 'Une réunion entièrement inventée'}, 'changes': [],
            'criteria': {'eliminatory': [], 'obligations': ['Conserver les décisions'], 'quality': []},
            'criteria_rule': 'Chaque obligation compte', 'package_sha256': 'a' * 64,
            'privacy': {'csrf_token': 'privacy-token', 'session_expires_at': '2099-01-01T00:00:00Z',
                        'dossier_id': 'd1', 'content_version': 1,
                        'contribution': {'enabled': False, 'revision': 0, 'example_revision': 1}}}


def browser_views():
    """États contrôlés servis par le harnais Chromium existant, sans appel fournisseur"""
    from copy import deepcopy
    from unittest.mock import patch
    from benchmark_web import projection
    from tests.test_web_access import campaign_view
    available = dict(assistant_configured=True, admission_open=True, can_submit=True, reason='open')
    examples = {}
    import inspect
    import re
    from benchmark import preparation
    reasons = set(re.findall(r"reason = '(\w+)'", inspect.getsource(preparation.availability)))
    assert 'daily_cap' not in reasons
    for reason in reasons:
        examples['availability-' + reason] = dict(dossiers=[], availability=dict(
            available, can_submit=reason == 'open', reason=reason))
    for status in ('disconnected', 'connected', 'invalid', 'unavailable'):
        access = dict(kind='access', status=status, connected=status == 'connected',
                      limit_usd='25', limit_remaining_usd='12.50', reason='KEY_REJECTED')
        examples['access-' + status] = access
        examples['launch-' + status] = campaign_view(access)
    ready = campaign_view(dict(status='connected', limit_remaining_usd='12.50'))
    ready.update(launchable=True, estimate_total_usd='3.50', cap_usd='50.00', checks=[
        dict(key='example_validated', ok=True, detail='Exemple validé'),
        dict(key='example_qualified', ok=True, detail='Exemple qualifié', findings=[
            dict(kind='cohérence', severity='note', text='Quantité à confirmer')]),
        dict(key='configurations_available', ok=True, detail='Tous les modèles sont disponibles')])
    examples['launch-ready'] = ready
    blocked = deepcopy(ready)
    blocked.update(launchable=False)
    blocked['checks'][1].update(ok=False, detail='La qualification de l’exemple est requise')
    blocked['checks'][2].update(ok=False, detail='Modèle à choisir de nouveau')
    examples['launch-blocked'] = blocked
    for estimate in ('0.125', None):
        value = deepcopy(ready)
        value.update(launchable=False, checks=[], judgment_estimate_usd=estimate)
        examples['estimate-' + ('known' if estimate else 'unknown')] = value
    examples['launch-judgment-blocked'] = dict(ready, launchable=False, checks=[
        dict(key='judgment_available', ok=False, detail='Budget insuffisant pour l’évaluation')])
    for name, state, admission, incident in (
            ('running', 'EMISSION_POSSIBLE', True, None), ('queued', 'INTENT_RECORDED', True, None),
            ('stopped', 'EMISSION_POSSIBLE', False, None), ('ambiguous', 'AMBIGUOUS', True, None),
            ('incident', 'RECEIVED', True, 'LENGTH'), ('received', 'RECEIVED', True, None)):
        value = deepcopy(ready)
        value.update(launchable=False)
        value['campaign'].update(task={'revision': 2}, admission_open=admission,
            attempts=[dict(state=state, incident=incident)], cells=[dict(configuration_id='x', state=state)],
            panel=[dict(id='x', model='Modèle A')])
        examples['followup-' + name] = value
    # #525, #533 : crédit épuisé sur le premier essai, campagne arrêtée, second modèle jamais interrogé
    value = deepcopy(examples['followup-incident'])
    value['campaign'].update(admission_open=False, stop_reason='ACQUISITION_EVIDENCE_INCOMPLETE',
        attempts=[dict(state='RECEIVED', incident='CREDIT_EXHAUSTED'), dict(state='INTENT_RECORDED', incident=None)],
        cells=[dict(configuration_id='x', state='RECEIVED'), dict(configuration_id='y', state='INTENT_RECORDED')],
        panel=[dict(id='x', model='Modèle A'), dict(id='y', model='Modèle B')])
    examples['followup-credit-exhausted'] = value
    # #537 : limite de débit reçue sur l'API officielle, campagne arrêtée sans relance préautorisée
    value = deepcopy(examples['followup-incident'])
    value['campaign'].update(admission_open=False, stop_reason='ACQUISITION_EVIDENCE_INCOMPLETE',
        attempts=[dict(cell_id='x1', state='RECEIVED', incident='RATE_LIMITED')],
        cells=[dict(cell_id='x1', configuration_id='x', state='RECEIVED')],
        panel=[dict(id='x', model='Modèle A', channel_id='https://api.z.ai/api/paas/v4/chat/completions')])
    examples['followup-official-limited'] = value
    # #539 : campagne opérateur, modèle épuisé à sa dernière tentative, sans évaluation automatique
    value = deepcopy(examples['followup-incident'])
    value['campaign'].update(attempts=[dict(cell_id='x1', state='RECEIVED', incident='LENGTH')], unusable_cells=['x1'],
        closed_cells=[], cells=[dict(cell_id='x1', configuration_id='x', state='RECEIVED')])
    examples['followup-operator-exhausted'] = value
    for status in ('NOT_STARTED', 'WAITING', 'RUNNING', 'COMPLETE', 'BLOCKED'):
        value = deepcopy(examples['followup-received'])
        value['campaign']['judgment'] = dict(status=status, total=1, completed=int(status == 'COMPLETE'),
            reason='Budget insuffisant' if status == 'BLOCKED' else None, can_start=status == 'NOT_STARTED')
        examples['judgment-' + status] = value
    for state, judgment in (('EMISSION_POSSIBLE', None), ('INTENT_RECORDED', None),
                           ('EMISSION_POSSIBLE', 'WAITING'), ('RECEIVED', None), ('RECEIVED', 'BLOCKED')):
        value = deepcopy(examples['followup-received'])
        value['campaign'].update(attempts=[dict(state='RECEIVED', incident='EMPTY_OUTPUT'), dict(state=state)],
            cells=[dict(configuration_id='x', state='RECEIVED'), dict(configuration_id='y', state=state)],
            panel=[dict(id='x', model='Modèle A'), dict(id='y', model='Modèle B')])
        if judgment:
            value['campaign']['judgment'] = dict(status=judgment, total=2, completed=0,
                reason='Évaluation interrompue' if judgment == 'BLOCKED' else None, can_start=False)
        examples['mixed-' + state + '-' + str(judgment)] = value
    examples['configurations-selected'] = {'assumptions': {},
     'available_tiers': ['low', 'high'],
     'cap_source': 'default',
     'cap_usd': '50.00',
     'configurations': [{'estimate': {'amount_usd': '1.20'}, 'model': 'modele-a'},
                        {'effort_limit': 'not_adjustable',
                         'estimate': {'amount_usd': '2.30'},
                         'model': 'modele-b'}],
     'current_campaign_id': 'd1-c1',
     'current_tier': 'high',
     'dossier_id': 'd1',
     'estimate_available': True,
     'estimate_total_usd': '3.50',
     'fetched_at': '2026-09-15T12:00:00+00:00',
     'kind': 'configurations',
     'models': [{'chosen': 'xhigh',
                 'id': 'modele-a',
                 'levels': ['medium', 'xhigh'],
                 'name': 'Modèle A',
                 'not_adjustable': False,
                 'selected': True},
                {'id': 'modele-b', 'name': 'Modèle B', 'not_adjustable': True, 'selected': True}],
     'superseded': []}
    for stamp in ('2026-09-16T12:00:01+00:00', '2026-09-16T12:00:02+00:00', '2026-09-15T12:00:00+00:00'):
        value = campaign_view(dict(status='connected'))
        value['campaign']['conditions']['frozen_at'] = stamp
        examples['date-' + stamp[8:10] + '-' + stamp[17:19]] = value
    examples['configurations-empty'] = dict(kind='configurations', dossier_id='d1',
        catalogue_available=False, models=[], detail='Relevé de modèles indisponible', configurations=[])
    examples['waiting'] = dict(operation_id='op', dossier_id='d1', availability=available)
    examples['honeypot'] = dict(kind='honeypot_ack')
    examples['listing'] = dict(dossiers=[dict(dossier_id='d1', need='Trier des notes', revision=2)], availability=available)
    examples['request-error'] = dict(error='Ce texte est trop court.', error_field='request',
        form=dict(dossier_id='d', action_id='a', request='court', useful='', context=''))
    examples['message-error'] = dict(error='Ce texte est trop long.', error_field='message',
        form=dict(action_id='a', revision=1, kind='clarify', message='long'))
    base = dict(dossier_id='d1', revision=2, current_revision=3, stage='preview', package=None,
        validation=None, qualified=False, explanation='Exemple attendu', availability=available,
        payload=dict(request='Besoin entièrement fictif', clarifications=[], validated_assumptions=[],
                     reformulation='', fictional_parameters={}))
    examples['prior-example'] = base
    examples['scope'] = dict(base, stage='scope_confirmation', current_revision=2,
        explanation='Acceptez-vous un exemple avec des pièces textuelles inventées ?')
    for category in ('math', 'coding', 'other'):
        examples['out-of-scope-' + category] = dict(base, stage='suspended',
            checks=dict(out_of_scope=category), explanation='Un test générique, sans tâche de travail à comparer')
    for reason in ('open', 'interrupted', 'restore'):
        examples['suspended-' + reason] = dict(base, current_revision=2, stage='suspended',
            explanation='Incident chez OpenRouter ou chez le fournisseur du modèle.',
            availability=dict(available, can_submit=reason == 'open', reason=reason))
    for status in ('PENDING', 'BLOCKED', 'QUALIFIED'):
        value = example_view()
        value.update(availability=available, validation={'validated_at': '2026-09-16T08:00:00Z'},
            qualified=status == 'QUALIFIED', qualification=dict(status=status, qualification_status=status,
                approval_status=status, summary='<img src=x onerror="alert(1)">Correction requise', findings=[]))
        if status == 'BLOCKED':
            value.update(package=None)
            value['qualification']['operation_id'] = 'op'
        examples['example-' + status] = value
    for stage, qualification, prior, pending in (
            ('waiting', {}, False, True), ('waiting', {}, True, False), ('clarification', {}, False, False),
            ('suspended', {}, False, False), ('preview', {'operation_id': 'op', 'status': 'PENDING'}, False, True),
            ('preview', {'operation_id': 'op', 'status': 'BLOCKED'}, False, False),
            ('preview', {'operation_id': 'op', 'status': 'QUALIFIED'}, False, False)):
        value = dict(base, revision=1, current_revision=2 if prior else 1, stage=stage,
            qualification=dict(qualification, summary='Contrôle', findings=[]),
            validation={'validated_at': '2026-09-17'} if qualification else None,
            qualified=qualification.get('status') == 'QUALIFIED',
            availability=dict(available, reason='waiting', can_submit=False))
        examples[f'progress-{stage}-{qualification.get("status", "none")}-{prior}'] = value
    # Exemple présent et vérification automatique en attente : l'état et son suivi passent dans la synthèse
    pending = example_view()
    pending.update(qualification=dict(operation_id='op', status='PENDING', summary='Contrôle', findings=[]),
                   validation={'validated_at': '2026-09-17'}, availability=dict(available, reason='waiting', can_submit=False))
    examples['progress-package-PENDING'] = pending
    history = example_view()
    stopped = deepcopy(ready['campaign'])
    stopped['conditions'].update(pi=dict(package='pi', version='1', status='PINNED'),
                                 packages=[], tools=[], skills=[], defaults={}, environment={})
    stopped.update(task=dict(version=1, revision=1), admission_open=False, allowed_cells=[],
        restore_pending=True, stop_reason=None, missing_authorities=['exécution', 'appels candidats', 'budget'], cost_basis={})
    history['campaigns'] = [stopped]
    examples['stopped-history'] = history
    metadata = deepcopy(example_view()['privacy'])
    metadata.update(content_version=4, contribution=dict(enabled=False, revision=2, example_revision=1))
    examples['privacy-listing'] = dict(dossiers=[], privacy=metadata)
    for name, expired, prior in (('current', False, False), ('expired', True, False), ('prior', False, True)):
        value = example_view()
        value.update(revision=3, current_revision=4 if prior else 3)
        value['privacy']['contribution'].update(revision=5, example_revision=3)
        if expired:
            value['privacy']['session_expires_at'] = '2000-01-01T00:00:00Z'
        examples['consent-' + name] = value
    for status, expiry in (('active', '2099-03-18'), ('withdrawn', '2099-03-18'),
                           ('expired', '2000-03-18'), ('active', '2000-03-18'), ('UNKNOWN_STATE', '2099-03-18')):
        examples['contribution-' + status + '-' + expiry[:4]] = dict(kind='contributions', csrf_token='purpose-only',
            contributions=[dict(id='opaque-identifier', created_at='2026-09-18T12:00:00Z',
                                expires_at=expiry + 'T00:00:00Z', status=status)])
    for name, path in (('safe', '/preparation?view=1'), ('unsafe', '//evil.test')):
        examples['bootstrap-' + name] = dict(kind='session_bootstrap', return_path=path)
    # #465 : copie privée d’une épreuve publiée, sans package ni qualification importée
    copy = deepcopy(example_view())
    copy.update(stage='draft', package=None, validation=None, qualified=False, operations=0,
                availability=dict(assistant_configured=True, admission_open=False, can_submit=False, reason='access'),
                explanation='Copie d’une épreuve publiée : décrivez votre besoin.')
    copy['payload'] = dict(copy['payload'], request='')
    copy.pop('criteria', None)
    copy['source'] = dict(task_id='tache-fictive', version_id='version-1', title='Tableau <script>alert(1)</script>',
                          work='Préparer un tableau par opération', cases=['Cinq débits inventés'],
                          limits=['Ne certifie aucune comptabilité'], qualification_status='QUALIFIED',
                          pieces=[dict(id='consigne', label='Consigne',
                                       text='Pièce source <script>alert(1)</script>')])
    copy['source_notice'] = dict(withdrawn=False, reports=[dict(
        summary='Erreur signalée <script>alert(2)</script>', reported_at='2026-10-23T10:00:00+00:00')])
    examples['copy-dossier'] = copy
    examples['copy-confirmation'] = {'kind': 'copy_source', 'source': copy['source'],
                                     'source_notice': copy['source_notice']}
    rendered = {name: views.render(value, 'csrf', error=bool(value.get('error'))).decode()
                for name, value in examples.items()}
    for name, source, version in (('checkout', 'abcdef0123456789', ''),
                                  ('release', 'abcdef0123456789', '0.2.0'), ('unknown', '', '0.2.0')):
        with patch.object(views, 'SOURCE_SHA', source), patch.object(views, 'RELEASE_VERSION', version):
            rendered['identity-' + name] = views.render({'kind': 'home'}, '').decode()
    rendered['publication-footer'] = projection.public_page(dict(need='Besoin fictif',
        task=dict(dossier_id='d', version=1), campaign_id='c', result_expected='Résultat fictif',
        conclusion=dict(text='Conclusion fictive', limits=[]), coverage={}, population={}, conditions={},
        cost_basis={}, economic_status='COMPLETE', rows=[], columns=[]), {}).decode()
    rendered['availability-reasons'] = sorted(reasons)
    return rendered


def results_view(*, withdrawn=False, unavailable=None, filtered=False):
    """Restitution publique fermée, sans accès privé : fixture du harnais Chromium"""
    cases = [{'position': 1}]
    panel = [
        {'position': 1, 'provider': 'openai', 'model': 'openai/gpt-5.6-sol', 'name': 'Modèle <script>alert(1)</script>',
         'revision': 'rev-a', 'access': 'API', 'channel_id': 'openrouter', 'route': '/chat/completions',
         'parameters': {'max_tokens': 4096}, 'effort': 'high'},
        {'position': 2, 'provider': 'mistralai', 'model': 'mistralai/mistral-small-2603', 'name': 'Modèle B',
         'revision': 'rev-b', 'access': 'API', 'channel_id': 'openrouter', 'route': '/chat/completions',
         'parameters': {'max_tokens': 4096}, 'effort': 'low'},
    ]
    columns = [
        {'id': 'cost', 'label': 'Coût observé', 'unit': 'USD', 'favorable': 'lower',
         'proof': 'Coût observé et source du reçu candidat', 'cost': True},
        {'id': 'criterion-2', 'label': 'Clarté du tableau', 'unit': 'descriptif', 'favorable': 'higher',
         'proof': 'Passages de la sortie cités exactement', 'cost': False},
    ]
    configuration = {
        'requested': {'provider': 'openrouter', 'model': 'openai/gpt-5.6-sol', 'revision': 'rev-a',
                      'access': 'API', 'channel_id': 'openrouter', 'route': '/chat/completions',
                      'parameters': {'max_tokens': 4096}, 'effort': 'high'},
        'observed': {'provider': 'fournisseur-observé', 'model': 'INCONNU', 'revision': 'rev-a',
                     'access': 'INCONNU', 'channel_id': 'openrouter', 'route': 'INCONNU',
                     'parameters': 'INCONNU', 'effort': 'INCONNU'},
        'sources': {'provider': 'HTTP response JSON', 'model': 'INCONNU', 'revision': 'HTTP response JSON',
                    'access': 'INCONNU', 'channel_id': 'request parameter', 'route': 'INCONNU',
                    'parameters': 'INCONNU', 'effort': 'INCONNU'}}
    rows = [
        {'panel_position': 1, 'case_position': 1, 'verdict': 'SATISFAIT', 'reason': 'Toutes les exigences sont respectées.',
         'configuration': configuration,
         'next_action': None,
         'findings': [{'label': 'Toutes les actions présentes', 'status': 'PASS',
                       'finding': 'Les actions et leur responsable figurent dans le tableau <script>alert(2)</script>.'}],
         'measures': [{'label': 'Clarté du tableau', 'value': 'excellent', 'unit': 'descriptif',
                       'status': 'KNOWN', 'reason': None}],
         'metrics': {'cost': {'value': '0.10', 'unit': 'USD', 'rank': 1, 'reason': None},
                     'criterion-2': {'value': 'excellent', 'unit': 'descriptif', 'rank': 1, 'reason': None}},
         'states': {'O1': 'PASS'}, 'created_at': '2026-10-23T10:00:00+00:00', 'responsible': 'Ayo',
         'judgment': {'mode': 'assisted', 'professional_review': None,
                      'cost': {'value': '0.02', 'unit': 'USD', 'status': 'KNOWN'}},
         'limits': ['Un exemple inventé ne prouve pas une fiabilité générale.'],
         'proofs': [{'name': 'Réponse de Modèle <script>alert(1)</script>', 'piece_id': 'output-a'},
                    {'name': 'Pièce réservée à l’évaluation', 'piece_id': None}]},
        {'panel_position': 2, 'case_position': 1, 'verdict': 'NE SATISFAIT PAS',
         'reason': 'Une exigence n’est pas respectée : les actions sont omises.', 'next_action': None,
         'configuration': configuration,
         'findings': [{'label': 'Toutes les actions présentes', 'status': 'FAIL',
                       'finding': 'Une action annoncée par la consigne manque au tableau.'}],
         'measures': [{'label': 'Clarté du tableau', 'value': 'faible', 'unit': 'descriptif',
                       'status': 'KNOWN', 'reason': None}],
         'metrics': {'cost': {'value': None, 'unit': 'USD', 'rank': None, 'reason': 'Coût INCONNU : reçu ou mesure absent'},
                     'criterion-2': {'value': 'faible', 'unit': 'descriptif', 'rank': 2, 'reason': None}},
         'states': {'O1': 'FAIL'}, 'created_at': '2026-10-23T10:01:00+00:00', 'responsible': 'Ayo',
         'judgment': {'mode': 'local', 'professional_review': {'author': 'Camille', 'phase': 'après jugement',
                                                               'scope': 'tableau'},
                      'cost': {'value': None, 'unit': 'INCONNU', 'status': 'UNKNOWN'}},
         'limits': ['Sortie non admissible, quel que soit son coût.'],
         'proofs': [{'name': 'Réponse de Modèle B', 'piece_id': None}]},
    ]
    shown = [row for row in rows if row['panel_position'] == 1] if filtered else rows
    return {
        'kind': 'results', 'task_id': 'tache-fictive', 'version_id': 'version-resultats',
        'campaign_id': 'campagne-1', 'contract_sha256': 'b' * 64,
        'withdrawn': withdrawn, 'unavailable': unavailable,
        'title': 'Tableau <script>alert(1)</script>', 'work': 'Préparer un tableau par opération',
        'conclusion': {'text': 'Comparaison des réponses enregistrées, cas par cas.',
                       'attribution': 'Le verdict porte sur la configuration observée sous les conditions communes déclarées.',
                       'limits': ['Ces résultats portent sur un exemple inventé.',
                                  'Une preuve réservée au juge reste privée.']},
        'coverage': {'planned_cells': 2, 'attempted_cells': 2, 'evaluated_attempts': 2,
                     'decided_attempts': 2, 'not_started': 0},
        'economic_status': 'INCOMPLETE',
        'conditions': {'pi': {'package': 'pi', 'version': '0.85.1', 'status': 'active'},
                       'environment': {'node_version': 'v24.0.0'}, 'tools': [], 'packages': [], 'skills': [],
                       'frozen_at': '2026-10-23T09:00:00+00:00'},
        'cost_basis': {'scope': 'Une tentative par cellule', 'attempts': 'Sans reprise', 'unit': 'USD',
                       'conversion': None},
        'cases': cases, 'panel': panel, 'columns': columns,
        'obligations': [{'id': 'O1', 'label': 'Toutes les actions présentes', 'states': ['PASS', 'FAIL']}],
        'rows': shown, 'population': len(rows), 'filter_scope': {'configuration': '1'} if filtered else {},
        'pending': [], 'acquisition_dates': ['2026-10-23T10:00:00+00:00', '2026-10-23T10:01:00+00:00'],
        'unknowns': ['Coût observé inconnu pour 1 réponse : aucun rang de coût.',
                     'Comparaison des coûts incomplète : certains coûts observés manquent ou ne sont pas comparables.'],
        'restrictions': ['Vérification limitée pour le lecteur : les pièces non retenues restent privées.'],
        'pieces': {'output-a': {'file': 'piece-output-a.txt', 'sha256': 'a' * 64,
                                'label': 'Réponse de Modèle A'}},
        'campaigns': [{'campaign_id': 'campagne-1', 'title': 'Tableau <script>alert(1)</script>',
                       'work': 'Préparer un tableau par opération', 'frozen_at': '2026-10-23T09:00:00+00:00',
                       'pi': {'package': 'pi', 'version': '0.85.1', 'status': 'active'},
                       'environment': {'node_version': 'v24.0.0'}, 'cases': 1,
                       'contract_sha256': 'b' * 64, 'unit': 'USD',
                       'coverage': {'planned_cells': 2, 'attempted_cells': 2, 'evaluated_attempts': 2,
                                    'decided_attempts': 2, 'not_started': 0},
                       'economic_status': 'INCOMPLETE'}],
    }
