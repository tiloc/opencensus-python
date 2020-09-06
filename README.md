Fork of 
OpenCensus - A stats collection and distributed tracing framework
=================================================================

*OpenCensus for Python* OpenCensus provides a framework to measure a
server's resource usage and collect performance stats. This repository
contains Python related utilities and supporting software needed by
OpenCensus.

See the original project for more details: https://github.com/census-instrumentation/opencensus-python

## Purpose of this fork
This fork is making improvements for the specific combination of Django, PostgreSQL, and Azure Monitor.

## License
Licensed under the Apache 2.0 license (same as the original).

### General changes
* Improved debug and info logging for easier troubleshooting

### Added to Django Middleware Integration
* Tracing of Database calls including EXPLAIN plans
* Tracing of memcached caching access
* Tracking of User ID and Session ID for Azure Exporters
* Add tracebacks to database access and memcached access

Calls are properly modeled as dependencies and show on the Azure Monitor Application Maps and in the Performance section, as well as in the Usage section.

### Added to Django manage.py
* A fully instrumented alternative, called "tracemanage.py"

### Added to PostgreSQL tracing
* Report results of unsuccessful DB operations in the corresponding span
* Break down overall DB access into dedicated buckets for INSERT, SELECT, UPDATE, SELECT COUNT(*) operations

### Added to Azure Exporters
* Export of User ID and Session ID for "Usage" section in Azure Monitor

### Removed from Azure Exporters
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
### Azure Monitor Instrumentation Key
Set the environment variable `APPLICATIONINSIGHTS_CONNECTION_STRING` to your connection string. This will be picked up by all loggers, exporters, etc.

### Django Middleware
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
        'EXPORTER': 'opencensus.ext.azure.trace_exporter.AzureExporter()',
        'EXPLAIN': 'simple'
    }
}
```
`EXPLAIN` can be either `simple` or `analyze`. The latter will compare the plan to reality. *This operation only works on PostgreSQL and is expensive.*

### Memcached
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
**IMPORTANT** As this is a modified copy of a file from Django 2.2.14, it is somewhat limited in its compatibility. It is known to *NOT* work on early 2.2 releases. It cannot work on 1.11, and it has not been tested on 3.x.

### Django manage.py
Place `tracemanage.py` into the same location as the original `manage.py` of your project. Adapt this line, or set the DJANGO_SETTINGS_MODULE environment variable.
```python
    # TODO: Replace project_name with your project name
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{{ project_name }}.settings')
```

`tracemanage.py` will use the same configuration settings and logging configuration as the Django middleware. `OPENCENSUS` and `LOGGING` are mandatory configurations. `CACHES` is optional. `MIDDLEWARE` is not used.

**EXAMPLE:** The `importfhir.py` source code file illustrates how to instrument a management command for additional telemetry. Any other command without this additional instrumentation will still output considerable amounts of telemetry.
**IMPORTANT** Do not use `tracemanage.py` for the `runserver` command. Use regular `manage.py` with the Django middleware instead.

### Logging
OpenCensus comes with a Log Exporter for Azure. The following example configuration will add Azure log handling to the root logger.

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
        'azure': {
            'format': '%(traceId)s %(spanId)s %(message)s',
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
            'class': 'opencensus.ext.azure.log_exporter.AzureLogHandler',
            'formatter': 'azure',
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
