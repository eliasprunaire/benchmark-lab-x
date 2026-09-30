"""Vues de confidentialité sans session, secret ni accès au stockage"""
from .fragments import text

PRIVACY_SCRIPT = '<script type="module" src="/preparation/privacy.js"></script>'
LOCAL_WARNING = ('Votre navigateur peut effacer ces copies sans vous prévenir, par exemple '
                 'en navigation privée ou quand il manque de place. Exportez les cas que vous voulez garder. '
                 'Attention : toute personne qui utilise ce profil de navigateur peut aussi les lire.')


def render_privacy_page(value, csrf='', *, preparation=False):
    return 'Mes données', (
        '<section data-privacy-history><h2>Vos cas gardés dans ce navigateur</h2><p>' + LOCAL_WARNING + '</p>'
        '<p role="status" data-privacy-status>Vos copies s’affichent ici quand JavaScript est activé.</p>'
        '<div class="actions"><button type="button" class="sec" data-privacy-action="clear" hidden>'
        'Tout effacer et arrêter l’historique</button><button type="button" class="sec" '
        'data-privacy-action="enable" hidden>Activer l’historique local</button></div>'
        '<div data-privacy-list></div><noscript><p>Activez JavaScript pour voir et exporter '
        'les cas gardés dans ce navigateur.</p></noscript></section>'
        '<p>' + ('' if preparation else '<a href="/preparation">Mes cas d’usage et ma clé</a> · ') +
        '<a href="/preparation/contributions">Mes contributions</a> · '
        '<a href="/confidentialite">Confidentialité</a></p>')


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


def render_privacy_controls(value, csrf, contribution=''):
    """`contribution` : choix de contribution replié ici une fois la comparaison lancée"""
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
    content = '<details class="privacy-controls"' + attrs + '><summary>Vos données pour ce cas</summary>' + contribution
    content += '<p>Sans activité de votre part, vos cas ne sont plus accessibles après 7 jours. Votre clé ne l’est plus après 30 jours.</p>'
    expiry = privacy.get('session_expires_at')
    if expiry:
        content += '<p>Sans nouvelle activité, votre accès se ferme le <time datetime="' + text(expiry) + '">' + text(date_lisible_utc(expiry)) + '</time> (date calculée au chargement de la page).</p>'
    if not active:
        content += '<p>Votre accès à ce cas est fermé ou indisponible. Si vous en avez gardé une copie dans ce navigateur, vous pouvez encore la lire dans Mes données.</p>'
    if dossier:
        content += '<p role="status" data-privacy-status>Vos copies s’affichent ici quand JavaScript est activé.</p>'
        content += '<div class="actions"><button type="button" class="sec" data-privacy-action="archive" hidden>Garder une copie dans ce navigateur</button>'
        content += '<button type="button" class="sec" data-privacy-action="enable-case" hidden>Réactiver l’historique de ce cas</button></div>'
        content += privacy_form('delete', csrf, '/preparation/dossiers/' + quote(dossier, safe='') + '/delete', {},
            '<p>Ce bouton efface la copie de ce cas gardée dans ce navigateur et demande sa suppression '
            'sur le serveur, contribution comprise si vous en avez fait une. Des copies peuvent '
            'rester dans les sauvegardes du serveur après cette demande.</p>'
            '<noscript><p>Sans JavaScript, seule la suppression sur le serveur est demandée, contribution '
            'comprise. Pour effacer aussi la copie gardée dans ce navigateur, activez JavaScript puis passez par Mes données.</p></noscript>'
            '<button type="submit" class="sec">Supprimer ce cas d’usage</button>'
            '<p role="status" data-privacy-status></p>', disabled=not active)
    content += '<p>' + LOCAL_WARNING + '</p><p><a href="/preparation/data">Mes données</a> · '
    content += '<a href="/preparation/contributions">Mes contributions</a> · <a href="/confidentialite">Confidentialité</a></p></details>'
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
    body = ('<legend>Partager cet exemple avec Bench-X et le garder dans ce navigateur (facultatif)</legend><p>Cocher cette case a deux effets. '
            'D’abord, vous autorisez Cybrel, l’éditeur de Bench-X, à conserver cet exemple pendant 6 mois pour améliorer le service. '
            'Cette autorisation ne permet pas de le publier. '
            'Ensuite, vous activez l’historique local : ce navigateur garde une copie complète de chaque cas d’usage que vous ouvrez. '
            'Cette copie contient plus que la contribution, car elle inclut votre besoin, vos messages et les versions successives '
            'de l’exemple.</p><p>L’historique local a besoin de JavaScript ; sans lui, seule la contribution '
            'est enregistrée. Décocher la case arrête la contribution. L’historique local continue : vous pouvez le mettre en pause ou l’effacer dans '
            '<a href="/preparation/data">Mes données</a>. Votre choix ne change rien à votre accès à Bench-X. '
            '<a href="/confidentialite">Lire la politique de confidentialité</a>.</p>'
            '<label><input type="checkbox" name="enabled" value="true"' + (' checked' if enabled else '') + '>'
            ' Je partage cet exemple avec Bench-X et j’active l’historique local</label><button type="submit" class="sec">Enregistrer mon choix</button>'
            '<p role="status" data-privacy-status>' + ('Vous partagez cet exemple avec Bench-X.' if enabled else 'Vous ne partagez pas cet exemple.') + '</p>')
    return '<div class="privacy-consent">' + privacy_form('contribution', privacy.get('csrf_token', csrf),
        '/preparation/dossiers/' + quote(privacy['dossier_id'], safe='') + '/contribution',
        {key: contribution[key] for key in ('revision', 'example_revision')}, body,
        disabled=not active_session(privacy)) + '</div>'


def render_contributions(value):
    from datetime import datetime, timezone
    from .fragments import date_lisible_utc
    from urllib.parse import quote
    content = ('<p>Vous pouvez retirer votre consentement sans enregistrer de nouveau votre clé OpenRouter : ce navigateur garde un accès séparé, réservé à vos contributions.</p>'
               '<p>Retirer un consentement arrête seulement la contribution concernée. L’historique local continue, et '
               'les copies déjà gardées dans ce navigateur restent. Pour le mettre en pause ou les effacer, allez dans '
               '<a href="/preparation/data">Mes données</a>.</p>')
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
        label = {'active': 'Active', 'withdrawn': 'Retirée', 'expired': 'Expirée'}.get(status, 'État inconnu')
        content += '<section><h2>Contribution du ' + text(date_lisible_utc(item['created_at'])) + '</h2>'
        content += '<p>Conservée jusqu’au ' + text(date_lisible_utc(item['expires_at'])) + ' · ' + label + '.</p>'
        content += privacy_form('withdraw', value['csrf_token'], '/preparation/contributions/' + quote(item['id'], safe='') + '/withdraw', {},
            '<button type="submit" class="sec">Retirer mon consentement</button><p role="status" data-privacy-status></p>',
            disabled=status != 'active') + '</section>'
    if not value['contributions']:
        content += '<p>Ce navigateur n’a accès à aucune contribution.</p>'
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
        '<p role="status" data-privacy-status>Ouverture de votre espace dans ce navigateur…</p>'
        + '<form method="post" action="/preparation/session/open">'
        + hidden('return_path', urlsplit(target).path) + '<button type="submit">Continuer</button></form>'
        + '<p>Si rien ne se passe, les cookies de ce site sont peut-être bloqués : autorisez-les, puis choisissez Continuer. '
        'Cette étape ne lance aucun modèle.</p><noscript><p>Choisissez Continuer pour ouvrir votre espace.</p></noscript></section>')
