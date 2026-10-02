"""Django settings for the scout-alert-bridge service.

A TOM Toolkit project run as scheduled management commands: Scout candidates are
ingested via tom_dataservices' `rundataquery` (with a saved broad query) and
reconciled/settled by `tom_jpl`'s `updatescout`; `scout_publisher` derives and publishes
Rubin ToO candidate events to Kafka. Nothing serves HTTP in production; the standard TOM
pages and the Django admin are mounted (see bridge/urls.py) and double as a local
inspection surface.

Apps, middleware and authentication backends come from `tom_common.default_settings`
(the tomtoolkit >= 3.1 contract): 3.1 moved accounts onto django-allauth, and tom_common
imports it at startup, so a hand-maintained INSTALLED_APPS no longer boots.
"""

import os
from pathlib import Path

from tom_common.default_settings import *  # noqa: F401, F403

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-only-insecure-secret-key')
DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = TOMTOOLKIT_INSTALLED_APPS + [  # noqa: F405
    'tom_jpl',
    'scout_publisher',
]

SITE_ID = 1

MIDDLEWARE = TOMTOOLKIT_MIDDLEWARE  # noqa: F405

ROOT_URLCONF = 'bridge.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]

WSGI_APPLICATION = 'bridge.wsgi.application'

if os.environ.get('DB_HOST'):
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.postgresql',
            'NAME': os.environ.get('DB_NAME', 'scout_bridge'),
            'USER': os.environ.get('DB_USER', 'scout_bridge'),
            'PASSWORD': os.environ.get('DB_PASSWORD', ''),
            'HOST': os.environ['DB_HOST'],
            'PORT': os.environ.get('DB_PORT', '5432'),
            # e.g. DB_SSLMODE=require for AWS-hosted Postgres (RDS/Aurora)
            'OPTIONS': {'sslmode': os.environ.get('DB_SSLMODE', 'prefer')},
        }
    }
else:
    DATABASES = {
        'default': {
            'ENGINE': 'django.db.backends.sqlite3',
            'NAME': BASE_DIR / 'db.sqlite3',
        }
    }

AUTHENTICATION_BACKENDS = TOMTOOLKIT_AUTHENTICATION_BACKENDS  # noqa: F405

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'data'
MEDIA_URL = '/data/'

# AutoField, not Django's BigAutoField default: tom_base's apps ship migrations built
# under AutoField and don't declare default_auto_field of their own, so a BigAutoField
# project setting leaves them permanently "changed but unmigrated" -- unfixable, as the
# migrations would have to be written into site-packages. Matches tom_setup's own
# settings template. Our apps pin BigAutoField in their AppConfigs and are unaffected.
DEFAULT_AUTO_FIELD = 'django.db.models.AutoField'

CRISPY_TEMPLATE_PACK = 'bootstrap5'

# TOM Toolkit
EXTRA_FIELDS = []
TARGET_PERMISSIONS = 'OPEN'
AUTH_STRATEGY = 'READ_ONLY'
HOOKS = {
    'target_post_save': 'tom_common.hooks.target_post_save',
    'observation_change_state': 'tom_common.hooks.observation_change_state',
    'data_product_post_upload': 'tom_dataproducts.hooks.data_product_post_upload',
    'data_product_post_save': 'tom_dataproducts.hooks.data_product_post_save',
    'multiple_data_products_post_save': 'tom_dataproducts.hooks.multiple_data_products_post_save',
}
TOM_FACILITY_CLASSES = []
DATA_PRODUCT_TYPES = {
    'photometry': ('photometry', 'Photometry'),
    'spectroscopy': ('spectroscopy', 'Spectroscopy'),
}
DATA_PROCESSORS = {}

DATA_SERVICES = {
    'Scout': {
        'base_url': 'https://ssd-api.jpl.nasa.gov/scout.api',
    },
}

# scout_publisher
# Defaults to the -test topic deliberately: an unset or mistyped SCOUT_TOPIC_URL then
# publishes somewhere harmless rather than into the stream Rubin consumes. The
# production deployment sets Scout.scout-prod explicitly, where it is visible in review.
SCOUT_TOPIC_URL = os.environ.get('SCOUT_TOPIC_URL', 'kafka://kafka.scimma.org/Scout.scout-test')
SCOUT_QUERY_NAME = os.environ.get('SCOUT_QUERY_NAME', 'scout-bridge-broad')
BRIDGE_VERSION = '0.1.0'
SCOUT_API_VERSION = '1.3'
FILTER_CRITERIA_VERSION = 'SSSC-NEO-WG-v0.2'
SCHEMA_VERSION = '1.0'
