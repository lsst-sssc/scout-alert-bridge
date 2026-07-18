"""Django settings for the scout-alert-bridge service.

A headless TOM Toolkit project: `tom_jpl` provides Scout ingestion (via its
`ingest_scout` management command) and `scout_publisher` derives and publishes
Rubin ToO candidate events to Kafka. The only web surface is the Django admin,
used for operational inspection.
"""

import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent

SECRET_KEY = os.environ.get('SECRET_KEY', 'dev-only-insecure-secret-key')
DEBUG = os.environ.get('DEBUG', 'false').lower() == 'true'
ALLOWED_HOSTS = os.environ.get('ALLOWED_HOSTS', '*').split(',')

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django.contrib.sites',
    'django_tasks',
    'django_tasks.backends.database',
    'guardian',
    'tom_common',
    'django_comments',
    'django_bootstrap5',
    'crispy_bootstrap5',
    'crispy_forms',
    'rest_framework',
    'rest_framework.authtoken',
    'django_filters',
    'django_tables2',
    'django_gravatar',
    'django_htmx',
    'tom_targets',
    'tom_alerts',
    'tom_observations',
    'tom_dataproducts',
    'tom_dataservices',
    'tom_jpl',
    'scout_publisher',
]

SITE_ID = 1

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

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

AUTHENTICATION_BACKENDS = (
    'django.contrib.auth.backends.ModelBackend',
    'guardian.backends.ObjectPermissionBackend',
)

LANGUAGE_CODE = 'en-us'
TIME_ZONE = 'UTC'
USE_I18N = True
USE_TZ = True

STATIC_URL = 'static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
MEDIA_ROOT = BASE_DIR / 'data'
MEDIA_URL = '/data/'

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

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
TOM_ALERT_CLASSES = []
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
SCOUT_TOPIC_URL = os.environ.get('SCOUT_TOPIC_URL', 'kafka://kafka.scimma.org/lco.scout-neo-too-test')
SCOUT_QUERY_NAME = os.environ.get('SCOUT_QUERY_NAME', 'scout-bridge-broad')
BRIDGE_VERSION = '0.1.0'
SCOUT_API_VERSION = '1.3'
FILTER_CRITERIA_VERSION = 'SSSC-NEO-WG-v0.2'
SCHEMA_VERSION = '1.0'
