#!/usr/bin/env python
"""Django manage.py with major additions for OpenCensus tracing
"""
import os
import six
import sys

import logging

if __name__ == '__main__':
    try:
        from opencensus.common import configuration
        from opencensus.trace import config_integration
        from opencensus.trace.tracer import Tracer
        from opencensus.trace.samplers import ProbabilitySampler
        from opencensus.ext.azure.log_exporter import AzureLogHandler
        from opencensus.ext.azure.trace_exporter import AzureExporter
        import opencensus.ext.postgresql
    except ImportError as ierr:
        raise ImportError(
            "Couldn't import OpenCensus"
    ) from ierr

    # TODO: Replace project_name with your project name
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', '{{ project_name }}.settings')
    try:
        from django.core.management import execute_from_command_line
        import django
        import django.conf
    except ImportError as exc:
        raise ImportError(
            "Couldn't import Django. Are you sure it's installed and "
            "available on your PYTHONPATH environment variable? Did you "
            "forget to activate a virtual environment?"
        ) from exc

    # Get OpenCensus settings from settings.py
    settings = getattr(django.conf.settings, 'OPENCENSUS', {})
    settings = settings.get('TRACE', {})

    sampler = (settings.get('SAMPLER', None)
                    or samplers.ProbabilitySampler())
    if isinstance(sampler, six.string_types):
        sampler = configuration.load(sampler)

    exporter = settings.get('EXPORTER', None) or \
        print_exporter.PrintExporter()
    if isinstance(exporter, six.string_types):
        exporter = configuration.load(exporter)


    tracer = Tracer(
        exporter=exporter,
        sampler=sampler,
    )

    # Add tracing for PostgreSQL
    config_integration.trace_integrations(['postgresql'])

    # Add logging handler
    config_integration.trace_integrations(['logging'])
    logger = logging.getLogger(__name__)

    # TODO: Remove hard-coded Azure Log Handler
    handler = AzureLogHandler()
    handler.setFormatter(logging.Formatter('%(traceId)s %(spanId)s %(message)s'))
# TODO: Activating the handler currently causes indefinite hang
#    logger.addHandler(handler)
#    logger.warning("Test")

    # Run with tracing
    with tracer.span(name='manage.py/{0}'.format(sys.argv[1])):
        execute_from_command_line(sys.argv)
