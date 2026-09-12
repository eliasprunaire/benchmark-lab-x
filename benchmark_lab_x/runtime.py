"""Commandes locales ; assistance fournisseur uniquement sur sélection explicite."""
import argparse
from collections import Counter
from contextlib import closing, contextmanager
import fcntl
from hashlib import sha256
import json
import os
from pathlib import Path
import shutil
import sqlite3

from .storage import IntegrityError, Store, initialize, initialize_preparation, _unique_object, _private, _strict_json as encode


def private_path(path, directory=False):
    _private(Path(path).lstat(), directory=directory)


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def verify(store):
    proof = store.verify_storage()
    if not proof['integrity_ok'] or proof['orphan_files']:
        raise IntegrityError('Stockage incomplet ou altéré')
    return proof


@contextmanager
def worker_lock(store, *, shared=False):
    # The existing directory descriptor pins the same lock across processes
    # ponytail: one lock per database, per-worker locks if finer recovery is needed
    fd = store._root_fd
    fcntl.flock(fd, (fcntl.LOCK_SH if shared else fcntl.LOCK_EX) | fcntl.LOCK_NB)
    try:
        yield
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)


def status(root, store):
    marker = Path(root) / 'restore.json'
    restored = os.path.lexists(marker)
    if restored:
        private_path(marker)
        if json.loads(marker.read_text()) != {'state': 'RESTORED_RECONCILIATION_REQUIRED'}:
            raise IntegrityError('État de restauration inconnu')
    connection = store._s1_connection()
    extended = connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s2_control'").fetchone()
    opened = False
    if extended:
        from .preparation import admission
        opened = bool(admission(store))
    campaigns_open = 0
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone():
        campaigns_open = connection.execute('SELECT count(*) FROM s4_status WHERE admission_id IS NOT NULL').fetchone()[0]
        opened = opened or bool(campaigns_open)
    # Keep the exact health wire fields consumed by service.executor_health
    # Campaign admission contributes to the existing flag, not an extra field
    return {'admission': opened and not restored, 'restore_pending': restored,
            'operations': dict(Counter(row['state'] for row in store.inspect_operations()))}


def stop(root, store, reason, after_process_exit=False):
    connection = store._s1_connection()
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s2_control'").fetchone():
        from .preparation import close_admission
        close_admission(store)
    if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone():
        from .campaigns import close_admission
        close_admission(store, reason)
    if after_process_exit:
        campaigns = set()
        if connection.execute("SELECT 1 FROM sqlite_schema WHERE name='s4_control'").fetchone():
            campaigns = {row[0] for row in connection.execute('SELECT operation_id FROM s4_attempts')}
        for row in store.inspect_operations():
            if row['state'] == 'EMISSION_POSSIBLE' and row['operation_id'] not in campaigns:
                store.mark_ambiguous(row['operation_id'], reason)
        if campaigns:
            try:
                with worker_lock(store):
                    for row in store.inspect_operations():
                        if row['state'] == 'EMISSION_POSSIBLE' and row['operation_id'] in campaigns:
                            store.mark_ambiguous(row['operation_id'], reason)
            except BlockingIOError:
                # Service exit does not establish independent worker exit
                pass
    return status(root, store)


def hashes(root):
    result = {}
    for path in sorted(root.rglob('*')):
        private_path(path, directory=path.is_dir() and not path.is_symlink())
        if path.is_file():
            result[path.relative_to(root).as_posix()] = sha256(path.read_bytes()).hexdigest()
    return result


def exclusive_json(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    with os.fdopen(fd, 'w') as stream:
        stream.write(encode(value) + '\n')
        stream.flush()
        os.fsync(stream.fileno())
    sync_directory(path.parent)


def backup(root, destination):
    """Le verrou SQLite bloque les écrivains de pièces pendant toute la copie."""
    destination = Path(destination).absolute()
    root = Path(root)
    if destination.is_relative_to(root):
        raise IntegrityError('Sauvegarde requise hors des données sources')
    with closing(Store(root)) as store, worker_lock(store), closing(sqlite3.connect(root / 'metadata.sqlite3')) as lock:
        lock.execute('BEGIN IMMEDIATE')
        state = status(root, store)
        if state['admission'] or state['operations'].get('EMISSION_POSSIBLE', 0):
            raise IntegrityError('Maintenance et arrêt des appels requis')
        verify(store)
        destination.mkdir(mode=0o700)
        copied = destination / 'private'
        copied.mkdir(mode=0o700)
        shutil.copytree(root / 'pieces', copied / 'pieces')
        if state['restore_pending']:
            shutil.copy2(root / 'restore.json', copied / 'restore.json')
        fd = os.open(copied / 'metadata.sqlite3', os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with closing(sqlite3.connect((root / 'metadata.sqlite3').as_uri() + '?mode=ro', uri=True)) as source:
            with closing(sqlite3.connect(copied / 'metadata.sqlite3')) as target:
                source.backup(target)
        with closing(Store(copied)) as check:
            proof = verify(check)
        for path in copied.rglob('*'):
            fd = os.open(path, os.O_RDONLY)
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
        sync_directory(copied)
        exclusive_json(destination / 'backup.json', {'schema_version': proof['schema_version'], 'files': hashes(copied), 'operations': state['operations']})
    return verify_backup(destination)


def verify_backup(destination):
    destination = Path(destination).absolute()
    private_path(destination, directory=True)
    private_path(destination / 'backup.json')
    manifest = json.loads((destination / 'backup.json').read_text())
    if type(manifest) is not dict or set(manifest) != {'schema_version', 'files', 'operations'}:
        raise IntegrityError('Manifeste de sauvegarde invalide')
    private_path(destination / 'private', directory=True)
    if hashes(destination / 'private') != manifest['files']:
        raise IntegrityError('Sauvegarde altérée')
    with closing(Store(destination / 'private')) as store:
        proof = verify(store)
        state = status(destination / 'private', store)
    if state['operations'] != manifest['operations']:
        raise IntegrityError('Opérations de sauvegarde divergentes')
    if manifest['schema_version'] != proof['schema_version']:
        raise IntegrityError('Schéma de sauvegarde divergent')
    return {**proof, 'state': 'BACKUP_VERIFIED'}


def restore(source, destination):
    source = Path(source).absolute()
    destination = Path(destination).absolute()
    if destination.is_relative_to(source):
        raise IntegrityError('Restauration requise hors de la sauvegarde')
    verify_backup(source)
    manifest = json.loads((source / 'backup.json').read_text())
    # La cible neuve conserve toute base existante, y compris la source de secours
    shutil.copytree(source / 'private', destination)
    if hashes(destination) != manifest['files']:
        raise IntegrityError('Copie de restauration divergente')
    with closing(Store(destination)) as store:
        proof = verify(store)
        if not status(destination, store)['restore_pending']:
            exclusive_json(destination / 'restore.json', {'state': 'RESTORED_RECONCILIATION_REQUIRED'})
        stop(destination, store, 'RESTORED_RECONCILIATION_REQUIRED', after_process_exit=True)
    for path in destination.rglob('*'):
        fd = os.open(path, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)
    sync_directory(destination)
    sync_directory(destination.parent)
    return {**proof, 'state': 'RESTORED_ADMISSION_BLOCKED'}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    campaign_actions = ('create-campaign', 'inspect-campaign', 'admit-campaign', 'stop-campaign', 'resume-campaign')
    campaign_actions += ('inspect-attempt-status',)
    parser.add_argument('action', choices=campaign_actions + ('reserve-judgment', 'execute-judgment', 'inspect-judgment', 'inspect-pi', 'prepare-recovery', 'prepare-candidate-configuration', 'inspect-model-profile', 'reserve-candidate', 'execute-candidate', 'prepare-review', 'prepare-evaluation', 'evaluate-attempt', 'initialize-reconciliation', 'reconcile-cost', 'inspect-cost', 'forecast-prices', 'initialize-evaluations', 'inspect-evaluation', 'initialize-campaigns', 'inspect-qualification', 'approve-qualification', 'initialize-preparation', 'admit-preparation', 'initialize', 'verify', 'status', 'maintenance', 'quiescence', 'backup', 'verify-backup', 'restore', 'web', 'executor'))
    parser.add_argument('--data', type=Path)
    parser.add_argument('--authority', type=Path)
    parser.add_argument('--allow-owner-launch', action='store_true', help='Autoriser explicitement le propriétaire à déclencher les cellules admises')
    parser.add_argument('--destination', type=Path)
    parser.add_argument('--socket', type=Path)
    parser.add_argument('--public', type=Path)
    parser.add_argument('--listen', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8080)
    parser.add_argument('--preparation-assistant', metavar='ALIAS_OR_PROFILE',
                        help='Alias glm-5.3-flash ou chemin d’un profil JSON local')
    parser.add_argument('--judgment-profile', metavar='ALIAS_OR_PROFILE')
    parser.add_argument('--candidate-pi', action='store_true', help='Charger le transport candidat Pi/OpenRouter dans l’exécuteur privé')
    parser.add_argument('--candidate-provider', choices=('openrouter', 'anthropic', 'deepseek', 'zai'), default='openrouter',
                        help='Canal candidat explicitement admis ; les API officielles sont un dernier recours')
    parser.add_argument('--model')
    parser.add_argument('--pi-package', type=Path)
    parser.add_argument('--node', type=Path)
    parser.add_argument('--input-tokens', type=int)
    parser.add_argument('--output-tokens', type=int)
    parser.add_argument('--cached-input-tokens', type=int, default=0)
    args = parser.parse_args(argv)
    os.umask(0o077)
    try:
        if args.candidate_provider != 'openrouter' and args.action != 'execute-candidate':
            raise ValueError('Canal officiel réservé à une acquisition opérateur explicitement admise')
        if args.judgment_profile is not None and args.action not in ('reserve-judgment', 'execute-judgment'):
            raise ValueError('Profil réservé au jugement privé')
        if args.candidate_pi and args.action != 'executor':
            raise ValueError('Transport candidat réservé à l’exécuteur')
        if args.preparation_assistant is not None and args.action not in ('executor', 'forecast-prices'):
            raise ValueError('Assistant réservé à l’exécuteur')
        if args.action == 'inspect-pi':
            from .pi_openrouter import identity
            if args.pi_package is None or args.node is None:
                raise ValueError('Installation Pi et Node explicites requis')
            print(encode(identity(args.pi_package, args.node)))
            return 0
        if args.action == 'forecast-prices':
            from .openrouter_prices import forecast
            profile = None
            if args.preparation_assistant is not None:
                from .openrouter_preparation import configuration, load_profile
                profile = load_profile(args.preparation_assistant)
                if args.model != profile['model']:
                    raise ValueError('Modèle distinct du profil de préparation')
                if args.output_tokens != profile['parameters']['max_tokens']:
                    raise ValueError('Limite de sortie distincte du profil de préparation')
            result = forecast(args.model, args.input_tokens, args.output_tokens, args.cached_input_tokens)
            if profile is not None:
                configured = configuration(result, profile)
                result['preparation'] = {'requested_configuration': configured, 'reserve_amount': configured['reserve_usd']}
            print(encode(result))
            return 0
        if args.action in ('web', 'executor'):
            from .service import release_identity, serve_executor, serve_web
            if args.socket is None:
                raise ValueError('Socket requise')
            if args.action == 'web':
                if args.public is None or not 1024 <= args.port <= 65535:
                    raise ValueError('Projection et port requis')
                serve_web(args.listen, args.port, args.public, args.socket, release_identity())
            else:
                if args.data is None:
                    raise ValueError('Données requises')
                transport = None
                candidate_factory = None
                profile = None
                if args.preparation_assistant is not None:
                    from .openrouter_preparation import OpenRouterPreparation, load_profile
                    profile = load_profile(args.preparation_assistant)
                key = os.environ.pop('OPENROUTER_API_KEY', '') if args.preparation_assistant is not None or args.candidate_pi else ''
                if args.candidate_pi:
                    from .pi_openrouter import PiOpenRouter, identity
                    if args.pi_package is None or args.node is None:
                        raise ValueError('Installation Pi et Node explicites requis')
                    identity(args.pi_package, args.node)
                    PiOpenRouter(key, args.pi_package, args.node)
                    candidate_factory = lambda: PiOpenRouter(key, args.pi_package, args.node)
                if args.preparation_assistant is not None:
                    transport = OpenRouterPreparation(key, profile)
                serve_executor(args.data, args.socket, release_identity(), transport=transport, candidate_transport_factory=candidate_factory)
            return 0
        if args.data is None:
            raise ValueError('Données requises')
        if args.action == 'initialize':
            if args.data.exists() and any(args.data.iterdir()):
                raise FileExistsError('Initialisation réservée à un emplacement vide')
            initialize(args.data)
            result = {'state': 'INITIALIZED_ADMISSION_BLOCKED'}
        elif args.action == 'initialize-preparation':
            initialize_preparation(args.data)
            result = {'state': 'PREPARATION_INITIALIZED_ADMISSION_BLOCKED'}
        elif args.action == 'initialize-campaigns':
            from .campaigns import initialize as initialize_campaigns
            initialize_campaigns(args.data)
            result = {'state': 'CAMPAIGNS_INITIALIZED'}
        elif args.action == 'initialize-evaluations':
            from .evaluation import initialize as initialize_evaluations
            initialize_evaluations(args.data)
            result = {'state': 'EVALUATIONS_INITIALIZED_REAL_JUDGMENT_CLOSED'}
        elif args.action == 'verify-backup':
            result = verify_backup(args.data)
        elif args.action in ('backup', 'restore'):
            if args.destination is None:
                raise ValueError('Destination requise')
            result = (backup if args.action == 'backup' else restore)(args.data, args.destination)
        else:
            with closing(Store(args.data)) as store:
                if args.action == 'inspect-attempt-status':
                    from .evaluation import attempt_status
                    from .storage import _fields
                    if args.authority is None:
                        raise ValueError('Fichier opérateur privé requis')
                    private_path(args.authority)
                    request = json.loads(args.authority.read_text(), object_pairs_hook=_unique_object)
                    _fields(request, ('campaign_id', 'attempt_id'), args.action)
                    result = attempt_status(store, request['campaign_id'], request['attempt_id'])
                elif args.action in ('reserve-judgment', 'execute-judgment', 'inspect-judgment'):
                    from . import judgment
                    from .storage import _fields
                    if args.authority is None:
                        raise ValueError('Fichier opérateur privé requis')
                    private_path(args.authority)
                    request = json.loads(args.authority.read_text(), object_pairs_hook=_unique_object)
                    if args.action == 'inspect-judgment':
                        _fields(request, ('operation_id',), args.action)
                        result = judgment.inspect(store, request['operation_id'])
                    else:
                        from .openrouter_judgment import OpenRouterJudgment
                        if args.judgment_profile is None:
                            raise ValueError('Profil de jugement explicite requis')
                        transport = OpenRouterJudgment(os.environ.pop('OPENROUTER_API_KEY', ''), args.judgment_profile)
                        if args.action == 'reserve-judgment':
                            result = judgment.reserve(store, request, transport)
                        else:
                            _fields(request, ('operation_id',), args.action)
                            judgment.execute(args.data, request['operation_id'], transport)
                            result = judgment.inspect(store, request['operation_id'])
                elif args.action in ('initialize-reconciliation', 'reconcile-cost', 'inspect-cost'):
                    with worker_lock(store):
                        verify(store)
                        if args.action == 'initialize-reconciliation':
                            result = store.initialize_reconciliation()
                        else:
                            if args.authority is None:
                                raise ValueError('Preuve ou identité opérateur privée requise')
                            private_path(args.authority)
                            with args.authority.open('rb') as stream:
                                raw = stream.read(2 * 1024 * 1024 + 1)
                            if len(raw) > 2 * 1024 * 1024:
                                raise ValueError('Preuve opérateur hors limites')
                            proof = json.loads(raw, object_pairs_hook=_unique_object)
                            if args.action == 'reconcile-cost':
                                result = store.reconcile_cost(proof)
                            else:
                                from .storage import _fields
                                _fields(proof, ('operation_id',), 'inspect-cost')
                                result = store.inspect_cost(proof['operation_id'])
                elif args.action in ('prepare-recovery', 'prepare-candidate-configuration', 'inspect-model-profile', 'reserve-candidate', 'execute-candidate', 'prepare-review', 'prepare-evaluation', 'evaluate-attempt'):
                    from . import campaigns, evaluation
                    from .storage import _fields
                    if args.authority is None:
                        raise ValueError('Fichier opérateur privé requis')
                    private_path(args.authority)
                    request = json.loads(args.authority.read_text(), object_pairs_hook=_unique_object)
                    if args.action == 'prepare-recovery':
                        from .recovery import propose
                        _fields(request, ('operation_id', 'capabilities'), args.action)
                        result = propose(store, request['operation_id'], request['capabilities'])
                    elif args.action == 'prepare-candidate-configuration':
                        from .recovery import starting_configuration
                        _fields(request, ('configuration', 'outgoing_format'), args.action)
                        result = starting_configuration(store, request['configuration'], content_format=request['outgoing_format'])
                    elif args.action == 'inspect-model-profile':
                        from .recovery import profile
                        _fields(request, ('provider', 'model', 'revision', 'access', 'channel_id', 'outgoing_format'),
                                args.action)
                        result = profile(store, request)
                    elif args.action == 'reserve-candidate':
                        _fields(request, ('campaign_id', 'cell_id', 'attempt_id'), args.action)
                        result = campaigns.reserve(store, request['campaign_id'], request['cell_id'], request['attempt_id'])
                    elif args.action == 'execute-candidate':
                        from .pi_openrouter import PiOpenRouter
                        _fields(request, ('campaign_id', 'attempt_id'), args.action)
                        if args.pi_package is None or args.node is None:
                            raise ValueError('Installation Pi et Node explicites requis')
                        before = campaigns.inspect(store, request['campaign_id'])
                        if request['attempt_id'] not in {a['operation_id'] for a in before['attempts']}:
                            raise ValueError('Tentative étrangère à la campagne')
                        if args.candidate_provider == 'openrouter':
                            transport = PiOpenRouter(os.environ.pop('OPENROUTER_API_KEY', ''), args.pi_package, args.node)
                        else:
                            from .pi_official import PiOfficial, CHANNELS
                            transport = PiOfficial(os.environ.pop(CHANNELS[args.candidate_provider][3], ''),
                                                   args.pi_package, args.node, args.candidate_provider)
                        campaigns.execute(args.data, request['attempt_id'], transport)
                        result = next(a for a in campaigns.inspect(store, request['campaign_id'])['attempts']
                                      if a['operation_id'] == request['attempt_id'])
                    elif args.action == 'prepare-review':
                        _fields(request, ('campaign_id', 'attempt_id'), args.action)
                        result = evaluation.prepare_review(store, request['campaign_id'], request['attempt_id'])
                    elif args.action == 'prepare-evaluation':
                        _fields(request, ('campaign_id', 'attempt_id'), args.action)
                        result = evaluation.prepare_report(store, request['campaign_id'], request['attempt_id'])
                    else:
                        result = evaluation.submit_report(store, request)
                elif args.action in campaign_actions:
                    from . import campaigns
                    from .storage import _fields
                    if args.authority is None:
                        raise ValueError('Fichier opérateur privé requis')
                    private_path(args.authority)
                    request = json.loads(args.authority.read_text(), object_pairs_hook=_unique_object)
                    if args.action == 'create-campaign':
                        _fields(request, ('manifest',), 'create-campaign')
                        result = campaigns.create(store, request['manifest'])
                    elif args.action == 'inspect-campaign':
                        _fields(request, ('campaign_id',), 'inspect-campaign')
                        result = campaigns.inspect(store, request['campaign_id'])
                    elif args.action == 'stop-campaign':
                        _fields(request, ('campaign_id', 'reason'), 'stop-campaign')
                        campaigns.stop(store, request['campaign_id'], request['reason'])
                        result = campaigns.inspect(store, request['campaign_id'])
                    else:
                        _fields(request, ('campaign_id', 'authority', 'evidence') + (('estimate',) if 'estimate' in request else ()), args.action)
                        purpose = 'resume' if args.action == 'resume-campaign' else 'start'
                        if type(request['authority']) is not dict or request['authority'].get('purpose') != purpose:
                            raise ValueError('Autorité distincte de lancement ou reprise requise')
                        result = campaigns.admit(store, request['campaign_id'], request['authority'], request['evidence'],
                                                 owner_launch=args.allow_owner_launch, estimate=request.get('estimate'))
                elif args.action == 'inspect-evaluation':
                    from .evaluation import inspect
                    from .storage import _fields
                    if args.authority is None:
                        raise ValueError('Fichier opérateur privé requis')
                    private_path(args.authority)
                    request = json.loads(args.authority.read_text(), object_pairs_hook=_unique_object)
                    _fields(request, ('evaluation_id',), 'inspect-evaluation')
                    result = inspect(store, request['evaluation_id'])
                elif args.action in ('inspect-qualification', 'approve-qualification'):
                    from .qualification import inspect_contract, approve
                    if args.authority is None:
                        raise ValueError('Fichier opérateur privé requis')
                    private_path(args.authority)
                    request = json.loads(args.authority.read_text(), object_pairs_hook=_unique_object)
                    if type(request) is not dict or 'contract_sha256' not in request:
                        raise ValueError('Contrat exact requis')
                    if args.action == 'inspect-qualification':
                        if set(request) not in ({'contract_sha256'}, {'contract_sha256', 'qualification_id', 'actor', 'authority'}):
                            raise ValueError('Requête opérateur invalide')
                        result = inspect_contract(store, request['contract_sha256'])
                    else:
                        if set(request) != {'contract_sha256', 'qualification_id', 'actor', 'authority'}:
                            raise ValueError('Requête opérateur invalide')
                        result = approve(store, request['contract_sha256'], request['qualification_id'],
                                         actor=request['actor'], authority=request['authority'])
                elif args.action == 'admit-preparation':
                    from .preparation import admit
                    if args.authority is None:
                        raise ValueError('Autorité requise')
                    private_path(args.authority)
                    authority = json.loads(args.authority.read_text(), object_pairs_hook=_unique_object)
                    admit(store, authority)
                    result = status(args.data, store)
                elif args.action == 'verify':
                    result = verify(store)
                elif args.action == 'maintenance':
                    result = stop(args.data, store, 'MAINTENANCE')
                elif args.action == 'quiescence':
                    with worker_lock(store), closing(sqlite3.connect(args.data / 'metadata.sqlite3', timeout=0)) as lock:
                        # S3 writers hold SQLite without the shared worker lock
                        # Closing rolls back the probe and releases it even on refusal
                        lock.execute('BEGIN IMMEDIATE')
                        result = status(args.data, store)
                        if result['admission'] or result['operations'].get('EMISSION_POSSIBLE', 0):
                            raise IntegrityError('Travaux encore actifs ou admission ouverte')
                else:
                    result = status(args.data, store)
        print(encode(result))
        return 78 if args.action == 'execute-candidate' and result['state'] != 'RECEIVED' else 0
    except (OSError, ValueError, KeyError, sqlite3.Error):
        # Ne pas copier le contenu d'une saisie ou un chemin privé dans les journaux
        print(encode({'state': 'HOLD', 'reason': 'OPERATION_NOT_VERIFIED'}))
        return 78


if __name__ == '__main__':
    raise SystemExit(main())
