import json

import jwt
import requests
from django.conf import settings
from django.core.cache import cache
from drf_spectacular.extensions import OpenApiAuthenticationExtension
from rest_framework.authentication import BaseAuthentication
from rest_framework.exceptions import AuthenticationFailed

from users.models import User, UserStatus


class SupabaseScheme(OpenApiAuthenticationExtension):
    target_class = 'users.authentication.SupabaseAuthentication'
    name = 'supabase'

    def get_security_definition(self, auto_schema):
        return {
            'type': 'http',
            'scheme': 'bearer',
            'bearerFormat': 'JWT',
            'description': 'Supabase JWT authentication. Obtain a token by signing in via the Supabase client.',
        }


class SupabaseAuthentication(BaseAuthentication):
    www_authenticate_realm = 'api'

    def authenticate(self, request):
        header = request.headers.get('Authorization')
        if not header or not header.startswith('Bearer '):
            return None

        token = header.removeprefix('Bearer ')
        payload = self._verify_jwt(token)

        email = payload.get('email', '')
        if not email:
            raise AuthenticationFailed('Email not found in token')

        user, _ = User.objects.get_or_create(
            email=email,
            defaults={'name': email.split('@')[0]},
        )

        if user.status != UserStatus.ACTIVE:
            raise AuthenticationFailed('User account is inactive')

        return (user, token)

    def _verify_jwt(self, token):
        try:
            jwks = self._get_jwks()
            header = jwt.get_unverified_header(token)
            kid = header.get('kid')

            key = None
            for jwk in jwks.get('keys', []):
                if jwk.get('kid') == kid:
                    kty = jwk.get('kty')
                    if kty == 'EC':
                        key = jwt.algorithms.ECAlgorithm.from_jwk(
                            json.dumps(jwk)
                        )
                    elif kty == 'RSA':
                        key = jwt.algorithms.RSAAlgorithm.from_jwk(
                            json.dumps(jwk)
                        )
                    else:
                        raise AuthenticationFailed(
                            f'Unsupported key type: {kty}'
                        )
                    break

            if not key:
                raise AuthenticationFailed('Unable to find signing key')

            payload = jwt.decode(
                token,
                key,
                algorithms=['ES256', 'RS256'],
                audience='authenticated',
                issuer=f'{settings.SUPABASE_PROJECT_URL}/auth/v1',
                options={'require': ['exp', 'sub']},
            )
            return payload
        except jwt.ExpiredSignatureError:
            raise AuthenticationFailed('Token has expired')
        except jwt.InvalidTokenError as e:
            raise AuthenticationFailed(f'Invalid token: {e}')

    def _get_jwks(self):
        jwks_url = (
            f'{settings.SUPABASE_PROJECT_URL}/auth/v1/.well-known/jwks.json'
        )
        cached = cache.get('supabase_jwks')
        if cached:
            return cached

        resp = requests.get(jwks_url, timeout=10)
        resp.raise_for_status()
        jwks = resp.json()
        cache.set('supabase_jwks', jwks, timeout=3600)
        return jwks
