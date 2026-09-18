"""Frontières du chiffrement des accès, uniquement avec des secrets fictifs"""
from base64 import urlsafe_b64decode
import json
import unittest

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from benchmark import provider_access as access
from benchmark.storage import IntegrityError


SECRET = b'\x11' * 32


class ProviderCryptoTests(unittest.TestCase):
    def test_aes256gcm_envelope_authenticates_session_identity_and_purpose(self):
        value = access.encrypt(SECRET, 'clé fictive', 'session-a', 'key')
        version, credential_id, payload = value.split(':')
        self.assertEqual('aes256gcm-v1', version)
        self.assertRegex(credential_id, r'^[0-9a-f]{32}$')
        raw = urlsafe_b64decode(payload)
        aad = json.dumps([version, 'session-a', credential_id, 'key'],
                         separators=(',', ':')).encode()
        self.assertEqual('clé fictive'.encode(), AESGCM(SECRET).decrypt(raw[:12], raw[12:], aad))
        self.assertIsNone(access.validate(value))
        self.assertEqual('clé fictive', access.decrypt(SECRET, value, 'session-a', 'key'))
        second = access.encrypt(SECRET, 'clé fictive', 'session-a', 'key')
        self.assertNotEqual(credential_id, second.split(':')[1])
        self.assertNotEqual(raw[:12], urlsafe_b64decode(second.split(':')[2])[:12])
        for secret, session, purpose in ((b'\x22' * 32, 'session-a', 'key'),
                                         (SECRET, 'session-b', 'key'),
                                         (SECRET, 'session-a', 'oauth')):
            with self.subTest(session=session, purpose=purpose, wrong_secret=secret != SECRET):
                with self.assertRaises(IntegrityError):
                    access.decrypt(secret, value, session, purpose)

    def test_legacy_is_read_only_through_explicit_pure_migration(self):
        legacy = ('AAECAwQFBgcICQoLDA0OD_cNulwbX3b_LA2oFwctDiyEuyDEz4dOqCfYJAqIweUk'
                  '6Vy74ta1xBfiQDE9eWpARQ9kfYsu0TQ=')
        with self.assertRaises(IntegrityError):
            access.decrypt(SECRET, legacy, 'session-a', 'key')
        with self.assertRaises(IntegrityError):
            access.validate(legacy)
        for purpose in ('key', 'oauth'):
            migrated = access.reencrypt_legacy(SECRET, legacy, 'session-a', purpose)
            self.assertIsNone(access.validate(migrated))
            self.assertEqual('sk-or-v1-legacy-fixture',
                             access.decrypt(SECRET, migrated, 'session-a', purpose))
            with self.assertRaises(IntegrityError):
                access.reencrypt_legacy(SECRET, migrated, 'session-a', purpose)
        for secret, cipher in ((b'\x22' * 32, legacy), (SECRET, legacy[:-5] + 'AAAA='),
                               (SECRET, legacy + '\n'), (SECRET, 'unknown:' + legacy)):
            with self.assertRaises(IntegrityError):
                access.reencrypt_legacy(secret, cipher, 'session-a', 'key')

    def test_migration_preserves_multiblock_legacy_key(self):
        legacy = 'AAECAwQFBgcICQoLDA0OD_cNulwbX3b_LFH8QlV6QjfV6mHR2JZP1NiNFxVJKVXV3VcWl8J3mXiTOke3zLSlZDee-Pcrl89WpcaiA_lWlavjIPtinUct-zftToQRPFIq7ELwqitbInUvb8ULQaK1Qms2Yn6yuXgkDA=='
        migrated = access.reencrypt_legacy(SECRET, legacy, 'session-a', 'key')
        self.assertEqual('sk-or-v1-' + '0123456789abcdef' * 4,
                         access.decrypt(SECRET, migrated, 'session-a', 'key'))

    def test_tampering_unknown_versions_and_malformed_wrappers_fail_closed(self):
        from base64 import urlsafe_b64encode
        value = access.encrypt(SECRET, 'fixture', 'session-a', 'key')
        version, credential_id, payload = value.split(':')
        malformed = [None, 42, '', value + ':extra', value.replace(version, 'aes256gcm-v2'),
                     value.replace(credential_id, 'g' * 32), value.replace(credential_id, 'a' * 31),
                     value + '\n', value + '=', ':'.join((version, credential_id, 'AAA=')),
                     ':'.join((version, credential_id, 'é'))]
        for cipher in malformed:
            with self.subTest(cipher=cipher):
                with self.assertRaises(IntegrityError):
                    access.validate(cipher)
                with self.assertRaises(IntegrityError):
                    access.decrypt(SECRET, cipher, 'session-a', 'key')
        # Chaque zone authentifiée : identité, nonce, texte chiffré, tag
        changed_id = ('0' if credential_id[0] != '0' else '1') + credential_id[1:]
        altered = [':'.join((version, changed_id, payload))]
        raw = urlsafe_b64decode(payload)
        for offset in (0, 12, len(raw) - 1):
            damaged = bytearray(raw)
            damaged[offset] ^= 1
            altered.append(':'.join((version, credential_id, urlsafe_b64encode(damaged).decode())))
        for cipher in altered:
            with self.assertRaises(IntegrityError):
                access.decrypt(SECRET, cipher, 'session-a', 'key')

    def test_secret_must_be_256_bits_and_binding_must_be_explicit(self):
        for secret in (None, b'x' * 16, b'x' * 24, b'x' * 31, b'x' * 33, '11' * 32):
            with self.assertRaises(ValueError):
                access.encrypt(secret, 'fixture', 'session-a', 'key')
        for session, purpose in (('', 'key'), (None, 'key'), ('session-a', 'KEY'),
                                  ('session-a', None), ('session-a', '')):
            with self.assertRaises(ValueError):
                access.encrypt(SECRET, 'fixture', session, purpose)
