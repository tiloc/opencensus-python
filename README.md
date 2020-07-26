Fork of 
OpenCensus - A stats collection and distributed tracing framework
=================================================================

*OpenCensus for Python* OpenCensus provides a framework to measure a
server's resource usage and collect performance stats. This repository
contains Python related utilities and supporting software needed by
OpenCensus.

See the original project for more details: https://github.com/census-instrumentation/opencensus-python

## Purpose of this fork
This fork is making improvements for the specific combination of Django and Azure Monitor.

## License
Licensed under the Apache 2.0 license (same as the original).

### General changes
* Improved debug and info logging for easier troubleshooting

### Added to Django Middleware Integration
* Tracing of Database calls including EXPLAIN plans
* Tracing of memcached caching access

Calls are properly modeled as dependencies and show on the Azure Monitor Application Maps and in the Performance section

### Removed
* Heartbeat functionality


## Installation
Clone source code with `git clone`and use `pip install -e` to install from source (typically into a venv)
```bash
pip install -e <repo-clone>/opencensus-python
pip install -e <repo-clone>/opencensus-python/context/opencensus-context
pip install -e <repo-clone>/opencensus-python/contrib/opencensus-ext-azure
pip install -e <repo-clone>/opencensus-python/contrib/opencensus-ext-django
pip install -e <repo-clone>/opencensus-python/contrib/opencensus-ext-logging
```

## Configuration
In `settings.py` add this (towards the end):
```python
MIDDLEWARE = [
...
    'opencensus.ext.django.middleware.OpencensusMiddleware',
...
]
```
This middleware will intercept Django activity and send it to Azure Monitor.

```python
OPENCENSUS = {
    'TRACE': {
        'SAMPLER': 'opencensus.trace.samplers.ProbabilitySampler(rate=1)',
        'EXPORTER': '''opencensus.ext.azure.trace_exporter.AzureExporter(
            connection_string="InstrumentationKey=<key from AI goes here>",
        )''',
        'EXPLAIN': 'simple'
    }
}
```
Set your instrumentation key where indicated.
`EXPLAIN` can be either `simple` or `analyze`. The latter will compare the plan to reality. This operation only works on PostgreSQL and is expensive.

```python
# Memcached
CACHES = {
    'default': {
        'BACKEND': 'opencensus.ext.django.memcached.MemcachedCache',
        'LOCATION': '127.0.0.1:11211',
        'KEY_PREFIX': 'tenant-1'
    }
}
```

Memcached tracing requires a special, instrumented cache backend. This is an instrumented copy of the backend from Django 2.2.14.

**IMPORTANT** This can only work in conjunction with the Middleware.

### Logging
OpenCensus comes with a Log Exporter for Azure. I have added logging to find out why it doesn't seem to ship any logs to Azure.

**IMPORTANT** The following configuration will not get this feature to work!
```python
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'formatters': {
        'verbose': {
            'format': '%(asctime)s %(levelname).3s %(process)d %(name)s : %(message)s',
        },
        'simple': {
            'format': '%(asctime)s %(levelname)-7s : %(message)s',
        },
    },
    'filters': {
        'require_debug_false': {
            '()': 'django.utils.log.RequireDebugFalse',
        }
    },
    'handlers': {
        'console': {
            'level': 'DEBUG',
            'class': 'logging.StreamHandler',
            'formatter': 'verbose',
        },
        'azure': {
            'level': 'INFO',
            'formatter': 'simple',
            'class': 'opencensus.ext.azure.log_exporter.AzureLogHandler',
            'connection_string': 'InstrumentationKey=<instrumentation key goes here>',
        },
        'mail_admins': {
            'level': 'ERROR',
            'filters': ['require_debug_false'],
            'class': 'django.utils.log.AdminEmailHandler'
        }
    },
    'loggers': {
        '': {
            'level': os.environ.get('LOGLEVEL', 'INFO'),
            'handlers': ['console', 'azure'],
        },
        'opencensus': {
            'handlers': ['console'],
            'level': 'DEBUG',
            'propagate': False,
        },
        'django': {
            'handlers': ['console'],
            'level': os.environ.get('LOGLEVEL', 'INFO'),
            'propagate': False,
        }
    },
}
```
