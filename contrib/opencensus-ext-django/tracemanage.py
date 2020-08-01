#!/usr/bin/env python
"""Django manage.py with major additions for OpenCensus tracing
"""
import os
import six
import sys

import logging
import logging.config

if __name__ == '__main__':
    try:
        from opencensus.common import configuration
        from opencensus.trace import config_integration, span as span_module
        from opencensus.trace.tracer import Tracer
        from opencensus.trace.samplers import ProbabilitySampler
        from opencensus.trace.print_exporter import PrintExporter
        from opencensus.ext.azure.log_exporter import AzureLogHandler
        from opencensus.ext.azure.trace_exporter import AzureExporter
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
    SETTINGS = getattr(django.conf.settings, 'OPENCENSUS', {})
    SETTINGS = SETTINGS.get('TRACE', {})

    SAMPLER = (SETTINGS.get('SAMPLER', None) or ProbabilitySampler(rate=1.0))
    if isinstance(SAMPLER, six.string_types):
        SAMPLER = configuration.load(SAMPLER)

    EXPORTER = SETTINGS.get('EXPORTER', None) or PrintExporter()
    if isinstance(EXPORTER, six.string_types):
        EXPORTER = configuration.load(EXPORTER)


    TRACER = Tracer(
        exporter=EXPORTER,
        sampler=SAMPLER,
    )

    # Add tracing for PostgreSQL
    config_integration.trace_integrations(['postgresql'])

    # Configure logging from settings.py
    logging.config.dictConfig(getattr(django.conf.settings, 'LOGGING', {}))

    # Add logging integration
    config_integration.trace_integrations(['logging'])
    logger = logging.getLogger(__name__)

    if getattr(django.conf.settings, 'DEBUG'):
        try:
            from logging_tree import printout
            printout()
        except:
            pass # optional logging_tree not in venv.

    # Run with tracing
    # TODO: Currently the manage.py command is showing as a dependency. Can I turn it into the node itself?
    # TODO: tracemanage.py node is showing with 0 calls and 0ms
    with TRACER.span(name='manage.py/{0}'.format(sys.argv[1])) as span:
        span.span_kind = span_module.SpanKind.CLIENT
        TRACER.add_attribute_to_current_span("http.method", "CLI")
        TRACER.add_attribute_to_current_span("http.route", 'manage.py/{0}'.format(sys.argv[1]))
        execute_from_command_line(sys.argv)
