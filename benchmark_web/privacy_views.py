"""Vues de confidentialité sans session, secret ni accès au stockage"""
from .fragments import text

PRIVACY_SCRIPT = '<script type="module" src="/preparation/privacy.js"></script>'
LOCAL_WARNING = ('L’historique local n’est pas garanti : le navigateur peut l’effacer, notamment '
                 'en navigation privée ou par manque de place. Exportez les cas à conserver. '
                 'Toute personne utilisant ce profil de navigateur peut les consulter.')


def render_privacy_page(value, csrf=''):
    if value['kind'] == 'privacy_notice':
        if value.get('privacy_enabled') is False:
            return 'Confidentialité', (
                '<p>La nouvelle politique de conservation n’est pas encore activée sur ce serveur. '
                'N’y saisissez aucune donnée personnelle ni information confidentielle.</p>'
                '<p>Responsable : Cybrel RSSI. Contact : '
                '<a href="mailto:contact@cybrel.fr">contact@cybrel.fr</a>.</p>')
        return 'Confidentialité', (
            '<section><h2>Responsable et contact</h2><p>Responsable : Cybrel RSSI. '
            'Pour une question ou une demande concernant vos données : '
            '<a href="mailto:contact@cybrel.fr">contact@cybrel.fr</a>.</p></section>'
            '<section><h2>Usage et conservation</h2><p>Vos saisies servent à préparer un exemple fictif '
            'et à comparer les modèles. Ne saisissez aucune donnée personnelle ni information confidentielle. '
            'Aucune anonymisation automatique n’est garantie.</p><ul>'
            '<li>Cas d’usage et résultats : l’accès est fermé après 7 jours d’inactivité. '
            'Cette fermeture n’est pas un effacement.</li>'
            '<li>Clé API chiffrée et accès de session : 30 jours d’inactivité. '
            'Seules vos interactions réelles prolongent cet accès, pas une page laissée ouverte.</li>'
            '<li>Contribution facultative : 6 mois, avec votre consentement après examen de l’exemple.</li>'
            '<li>Des copies peuvent subsister dans les sauvegardes après la fermeture de l’accès. '
            'Elles ne sont pas remises en service avant application des révocations et expirations.</li>'
            '</ul></section><section><h2>Historique dans ce navigateur</h2><p>' + LOCAL_WARNING + '</p>'
            '<p>Les copies complètes déjà enregistrées restent consultables ici même après expiration '
            'de la clé. La clé API n’est pas incluse dans cet historique ni dans les exports.</p></section>'
            '<section><h2>Fournisseurs et contribution</h2><p>Les données nécessaires aux appels sont '
            'transmises à Openrouter et aux fournisseurs des modèles utilisés. Leurs propres conditions '
            'de traitement et de conservation s’appliquent. Une suppression dans Bench-X ne garantit '
            'pas l’effacement de copies déjà traitées par ces fournisseurs.</p>'
            '<p>Contribuer est facultatif et sans effet sur votre accès au benchmark. Ce choix autorise '
            'Cybrel à conserver l’exemple affiché pour améliorer Bench-X pendant 6 mois. '
            'Il n’autorise pas sa publication. Vous pouvez retirer ce consentement depuis '
            '<a href="/preparation/contributions">Mes contributions</a>.</p></section>'
            '<p>La contribution comprend l’exemple fictif, ses pièces, sa qualification et ses résultats. '
            'Le besoin initial, vos messages et corrections libres, la clé, les cookies et les journaux bruts en sont exclus. '
            'Ces copies privées restent potentiellement sensibles. Aucun entraînement automatique n’est déclenché.</p>'
            '<p>L’accès aux contributions repose sur un cookie distinct, valable jusqu’à leur dernière échéance. '
            'Si vous perdez ce cookie, nous ne pouvons pas retrouver automatiquement votre accès ; contactez-nous.</p>'
            '<p><a href="/preparation/data">Mes données</a></p>')
    return 'Mes données', (
        '<section data-privacy-history><h2>Historique local</h2><p>' + LOCAL_WARNING + '</p>'
        '<p role="status" data-privacy-status>Chargement de l’historique local…</p>'
        '<div class="actions"><button type="button" class="sec" data-privacy-action="clear" hidden>'
        'Effacer tout l’historique local</button><button type="button" class="sec" '
        'data-privacy-action="enable" hidden>Réactiver l’historique local</button></div>'
        '<div data-privacy-list></div><noscript><p>Activez JavaScript pour consulter et exporter '
        'l’historique enregistré dans ce navigateur.</p></noscript></section>'
        '<p><a href="/preparation">Gérer ma clé et mes cas sur le serveur</a> · '
        '<a href="/preparation/contributions">Mes contributions</a> · '
        '<a href="/preparation/privacy">Confidentialité</a></p>')


def active_session(privacy):
    from datetime import datetime, timezone
    try:
        return (bool(privacy.get('csrf_token')) and datetime.fromisoformat(
            privacy['session_expires_at'].replace('Z', '+00:00')) > datetime.now(timezone.utc))
    except (KeyError, ValueError, TypeError, AttributeError):
        return False


def privacy_form(action, csrf, url, fields, body, *, disabled=False):
    from .fragments import form
    content = '<fieldset' + (' disabled' if disabled else '') + '>' + body + '</fieldset>'
    return form(csrf, url, fields, content).replace('<form ', '<form data-privacy-post="' + action + '" ', 1)


def render_privacy_controls(value, csrf):
    from .fragments import date_lisible_utc
    from urllib.parse import quote
    privacy = value.get('privacy')
    if not privacy:
        return ''
    csrf = privacy.get('csrf_token', csrf)
    active = active_session(privacy)
    dossier = privacy.get('dossier_id')
    attrs = ' data-privacy-controls data-csrf-token="' + text(csrf) + '"'
    if active:
        attrs += ' data-privacy-activity'
    if dossier:
        attrs += ' data-dossier-id="' + text(dossier) + '"'
        if type(privacy.get('content_version')) is int:
            attrs += ' data-content-version="' + str(privacy['content_version']) + '"'
    content = '<details class="privacy-controls"' + attrs + '><summary>Mes données et ma confidentialité</summary>'
    content += '<p>Accès aux cas fermé après 7 jours d’inactivité. Accès à la clé fermé après 30 jours d’inactivité.</p>'
    expiry = privacy.get('session_expires_at')
    if expiry:
        content += '<p>Expiration de l’accès au dernier chargement : <time datetime="' + text(expiry) + '">' + text(date_lisible_utc(expiry)) + '</time>.</p>'
    if not active:
        content += '<p>Accès expiré ou indisponible. Vos copies locales restent consultables dans Mes données.</p>'
    if dossier:
        content += '<p role="status" data-privacy-status>Historique local disponible avec JavaScript.</p>'
        content += '<div class="actions"><button type="button" class="sec" data-privacy-action="archive" hidden>Enregistrer une copie locale</button>'
        content += '<button type="button" class="sec" data-privacy-action="enable-case" hidden>Réactiver l’historique de ce cas</button></div>'
        content += privacy_form('delete', csrf, '/preparation/dossiers/' + quote(dossier, safe='') + '/delete', {},
            '<p>Cette action efface la copie locale de ce cas d’usage et demande sa suppression '
            'sur le serveur, ainsi que celle de sa contribution éventuelle. Des copies peuvent '
            'subsister dans les sauvegardes après cette demande.</p>'
            '<noscript><p>Sans JavaScript, seule la suppression sur le serveur et de la contribution '
            'est demandée. Effacez aussi la copie locale depuis Mes données avec JavaScript.</p></noscript>'
            '<button type="submit" class="sec">Supprimer ce cas d’usage</button>'
            '<p role="status" data-privacy-status></p>', disabled=not active)
    content += '<p>' + LOCAL_WARNING + '</p><p><a href="/preparation/data">Mes données</a> · '
    content += '<a href="/preparation/contributions">Mes contributions</a> · <a href="/preparation/privacy">Confidentialité</a></p></details>'
    return content


def render_contribution(value, csrf):
    from urllib.parse import quote
    privacy = value.get('privacy', {})
    contribution = privacy.get('contribution', {})
    if (not value.get('package') or not privacy.get('dossier_id') or not contribution
            or value.get('revision') != value.get('current_revision', value.get('revision'))
            or contribution.get('example_revision') != value.get('revision')):
        return ''
    enabled = contribution.get('enabled') is True
    body = ('<legend>Contribuer à Bench-X (facultatif)</legend><p>J’autorise Cybrel à conserver cet exemple '
            'pendant 6 mois pour améliorer Bench-X, sans autoriser sa publication. '
            'Mon choix ne conditionne pas mon accès au benchmark. '
            '<a href="/preparation/privacy">Lire la notice</a>.</p>'
            '<label><input type="checkbox" name="enabled" value="true"' + (' checked' if enabled else '') + '>'
            ' Je souhaite contribuer avec cet exemple</label><button type="submit" class="sec">Enregistrer mon choix</button>'
            '<p role="status" data-privacy-status>' + ('Contribution activée.' if enabled else 'Aucune contribution activée.') + '</p>')
    return '<div class="privacy-consent">' + privacy_form('contribution', privacy.get('csrf_token', csrf),
        '/preparation/dossiers/' + quote(privacy['dossier_id'], safe='') + '/contribution',
        {key: contribution[key] for key in ('revision', 'example_revision')}, body,
        disabled=not active_session(privacy)) + '</div>'


def render_contributions(value):
    from datetime import datetime, timezone
    from .fragments import date_lisible_utc
    from urllib.parse import quote
    content = '<p>Retirez votre consentement sans avoir à réactiver votre clé API. Ce navigateur conserve un accès de gestion distinct.</p>'
    for item in value['contributions']:
        status = str(item.get('status', '')).lower()
        if status == 'active':
            try:
                expiry = datetime.fromisoformat(item['expires_at'].replace('Z', '+00:00'))
                if expiry.tzinfo is None:
                    expiry = expiry.replace(tzinfo=timezone.utc)
                if expiry <= datetime.now(timezone.utc):
                    status = 'expired'
            except (KeyError, ValueError, TypeError, AttributeError):
                status = 'unknown'
        label = {'active': 'Active', 'withdrawn': 'Retirée', 'expired': 'Expirée'}.get(status, 'État indisponible')
        content += '<section><h2>Contribution du ' + text(date_lisible_utc(item['created_at'])) + '</h2>'
        content += '<p>Expiration : ' + text(date_lisible_utc(item['expires_at'])) + ' · ' + label + '.</p>'
        content += privacy_form('withdraw', value['csrf_token'], '/preparation/contributions/' + quote(item['id'], safe='') + '/withdraw', {},
            '<button type="submit" class="sec">Retirer mon consentement</button><p role="status" data-privacy-status></p>',
            disabled=status != 'active') + '</section>'
    if not value['contributions']:
        content += '<p>Aucune contribution accessible avec ce navigateur.</p>'
    return 'Mes contributions', content + '<p><a href="/preparation/data">Mes données</a></p>'


def render_bootstrap(value):
    from urllib.parse import urlsplit
    target = value.get('return_path', '/preparation')
    try:
        parsed = urlsplit(target)
        if (not target.startswith('/') or target.startswith('//') or parsed.netloc or parsed.scheme
                or '\\' in target or any(ord(char) < 32 for char in target)):
            target = '/preparation'
    except (ValueError, TypeError, AttributeError):
        target = '/preparation'
    from .fragments import hidden
    return 'Ouvrir mon espace', ('<section data-privacy-bootstrap data-return-path="' + text(target) + '">'
        '<p role="status" data-privacy-status>Ouverture de votre accès dans ce navigateur…</p>'
        + '<form method="post" action="/preparation/session/open">'
        + hidden('return_path', urlsplit(target).path) + '<button type="submit">Continuer</button></form>'
        + '<p>Si les cookies sont bloqués, autorisez les cookies de ce site puis choisissez Continuer. '
        'Aucun appel modèle n’est lancé.</p><noscript><p>Choisissez Continuer pour ouvrir votre accès.</p></noscript></section>')
