# Copyright 2017, OpenCensus Authors
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

# Prominent notice according to section 4b of the license: Tilo Christ modified this file.


"""Django middleware helper to capture and trace a request."""
import six

import logging

import django
import django.conf
from django.db import connection, connections
from django.utils.deprecation import MiddlewareMixin
from google.rpc import code_pb2

from opencensus.common import configuration
from opencensus.common import utils as common_utils
from opencensus.trace import (
    attributes_helper,
    execution_context,
    print_exporter,
    samplers,
)
from opencensus.trace import span as span_module
from opencensus.trace import status as status_module
from opencensus.trace import tracer as tracer_module
from opencensus.trace import utils
from opencensus.trace.propagation import trace_context_http_header_format

HTTP_HOST = attributes_helper.COMMON_ATTRIBUTES['HTTP_HOST']
HTTP_METHOD = attributes_helper.COMMON_ATTRIBUTES['HTTP_METHOD']
HTTP_PATH = attributes_helper.COMMON_ATTRIBUTES['HTTP_PATH']
HTTP_ROUTE = attributes_helper.COMMON_ATTRIBUTES['HTTP_ROUTE']
HTTP_URL = attributes_helper.COMMON_ATTRIBUTES['HTTP_URL']
HTTP_STATUS_CODE = attributes_helper.COMMON_ATTRIBUTES['HTTP_STATUS_CODE']

REQUEST_THREAD_LOCAL_KEY = 'django_request'
SPAN_THREAD_LOCAL_KEY = 'django_span'

BLACKLIST_PATHS = 'BLACKLIST_PATHS'
BLACKLIST_HOSTNAMES = 'BLACKLIST_HOSTNAMES'

logger = logging.getLogger(__name__)


class _DjangoMetaWrapper(object):
    """
    Wrapper class which takes HTTP header name and retrieve the value from
    Django request.META
    """

    def __init__(self, meta=None):
        self.meta = meta or _get_django_request().META

    def get(self, key):
        return self.meta.get('HTTP_' + key.upper().replace('-', '_'))


def _get_django_request():
    """Get Django request from thread local.

    :rtype: str
    :returns: Django request.
    """
    return execution_context.get_opencensus_attr(REQUEST_THREAD_LOCAL_KEY)


def _get_django_span():
    """Get Django span from thread local.

    :rtype: str
    :returns: Django request.
    """
    return execution_context.get_opencensus_attr(SPAN_THREAD_LOCAL_KEY)


def _get_current_tracer():
    """Get the current request tracer."""
    return execution_context.get_opencensus_tracer()


def _set_django_attributes(span, request):
    """Set the django related attributes."""
    django_user = getattr(request, 'user', None)

    if django_user is not None:
        user_id = django_user.pk

        # User id is the django autofield for User model as the primary key
        if user_id is not None:
            span.add_attribute('ai.user.id', str(user_id))
    
            user_name = django_user.get_username()
            if user_name is not None:
                span.add_attribute('ai.user.authUserId', str(user_name))

    if request.session is not None and request.session.session_key is not None:
        span.add_attribute('ai.session.id', request.session.session_key)


def _trace_db_call(execute, sql, params, many, context):
    explain_mode = execution_context.get_opencensus_attr('explain_mode')

    if "EXPLAIN" in sql:
        logger.debug("_trace_db_call: Processing EXPLAIN statement")
        return execute(sql, params, many, context)
    
    logger.debug(f"_trace_db_call: {sql}")

    tracer = _get_current_tracer()
    if not tracer:
        return execute(sql, params, many, context)

    vendor = context['connection'].vendor
    alias = context['connection'].alias

    span = tracer.start_span()
    try:
        (sql_command, subcommand, *_) = sql.split(maxsplit=2)
        logger.info(f"subcommand: '{subcommand}'")
        if "COUNT(*)" == subcommand:
            span.name = '{}.COUNT'.format(vendor)
        else:
            span.name = '{}.{}'.format(vendor, sql_command)                
    except Exception:
        span.name = '{}.OTHER'.format(vendor)
        logger.warning("Could not parse SQL statement for detailed tracing", exc_info=True)

    span.span_kind = span_module.SpanKind.CLIENT

    tracer.add_attribute_to_current_span('component', vendor)
    tracer.add_attribute_to_current_span('db.instance', alias)
    tracer.add_attribute_to_current_span('db.statement', sql)
    tracer.add_attribute_to_current_span('db.type', 'sql')
    tracer.add_attribute_to_current_span('python.traceback', common_utils.get_traceback())

    # EXPLAIN is expensive and needs to be explicitly enabled
    if explain_mode is not None:
        try:
            with connections[alias].cursor() as cursor:
                # EXPLAIN ANALYZE only works under certain circumstances
                if "analyze" == explain_mode and "postgresql" == vendor and "SELECT" == sql_command:
                    cursor.execute("EXPLAIN ANALYZE {0}".format(sql), params)
                else:
                    cursor.execute("EXPLAIN {0}".format(sql), params)
                planresult = cursor.fetchall()

            logger.debug("EXPLAIN plan: {0}".format(planresult))
            tracer.add_attribute_to_current_span('db.plan', "{0}".format(planresult))
        except Exception: # pragma: NO COVER
            logger.warning("Could not retrieve EXPLAIN plan", exc_info=True)
            tracer.add_attribute_to_current_span('db.plan', 'not available')
            pass        

    try:
        result = execute(sql, params, many, context)
    except Exception as exc:  # pragma: NO COVER
        status = status_module.Status.from_exception(exc)
        span.set_status(status)
        raise
    else:
        return result
    finally:
        tracer.end_span()


class OpencensusMiddleware(MiddlewareMixin):
    """Saves the request in thread local"""

    def __init__(self, get_response=None):
        self.get_response = get_response
        settings = getattr(django.conf.settings, 'OPENCENSUS', {})
        settings = settings.get('TRACE', {})

        self.sampler = (settings.get('SAMPLER', None)
                        or samplers.ProbabilitySampler())
        if isinstance(self.sampler, six.string_types):
            self.sampler = configuration.load(self.sampler)

        self.exporter = settings.get('EXPORTER', None) or \
            print_exporter.PrintExporter()
        if isinstance(self.exporter, six.string_types):
            self.exporter = configuration.load(self.exporter)

        self.propagator = settings.get('PROPAGATOR', None) or \
            trace_context_http_header_format.TraceContextPropagator()
        if isinstance(self.propagator, six.string_types):
            self.propagator = configuration.load(self.propagator)

        self.blacklist_paths = settings.get(BLACKLIST_PATHS, None)

        self.blacklist_hostnames = settings.get(BLACKLIST_HOSTNAMES, None)
    
        self.explain_mode = settings.get('EXPLAIN', None)

        logger.debug(f"OpenCensus Exporter: {self.exporter}")

    def __call__(self, request):
        if django.VERSION >= (2,):  # pragma: NO COVER
            with connection.execute_wrapper(_trace_db_call):
                return super(OpencensusMiddleware, self).__call__(request)
        return super(OpencensusMiddleware, self).__call__(request)

    def process_request(self, request):
        """Called on each request, before Django decides which view to execute.

        :type request: :class:`~django.http.request.HttpRequest`
        :param request: Django http request.
        """
        # Do not trace if the url is blacklisted
        if utils.disable_tracing_url(request.path, self.blacklist_paths):
            return

        # Add the request to thread local
        execution_context.set_opencensus_attr(
            REQUEST_THREAD_LOCAL_KEY,
            request)

        execution_context.set_opencensus_attr(
            'blacklist_hostnames',
            self.blacklist_hostnames)

        execution_context.set_opencensus_attr(
            'explain_mode',
            self.explain_mode
        )

        try:
            # Start tracing this request
            span_context = self.propagator.from_headers(
                _DjangoMetaWrapper(_get_django_request().META))

            # Reload the tracer with the new span context
            tracer = tracer_module.Tracer(
                span_context=span_context,
                sampler=self.sampler,
                exporter=self.exporter,
                propagator=self.propagator)

            # Span name is being set at process_view
            span = tracer.start_span()
            span.span_kind = span_module.SpanKind.SERVER
            tracer.add_attribute_to_current_span(
                attribute_key=HTTP_HOST,
                attribute_value=request.get_host())
            tracer.add_attribute_to_current_span(
                attribute_key=HTTP_METHOD,
                attribute_value=request.method)
            tracer.add_attribute_to_current_span(
                attribute_key=HTTP_PATH,
                attribute_value=str(request.path))
            tracer.add_attribute_to_current_span(
                attribute_key=HTTP_ROUTE,
                attribute_value=str(request.path))
            tracer.add_attribute_to_current_span(
                attribute_key=HTTP_URL,
                attribute_value=str(request.build_absolute_uri()))

            # Add the span to thread local
            # in some cases (exceptions, timeouts) currentspan in
            # response event will be one of a child spans.
            # let's keep reference to 'django' span and
            # use it in response event
            execution_context.set_opencensus_attr(
                SPAN_THREAD_LOCAL_KEY,
                span)

        except Exception:  # pragma: NO COVER
            logger.warning('Failed to trace request', exc_info=True)

    def process_view(self, request, view_func, *args, **kwargs):
        """Process view is executed before the view function, here we get the
        function name add set it as the span name.
        """

        # Do not trace if the url is blacklisted
        if utils.disable_tracing_url(request.path, self.blacklist_paths):
            return

        try:
            # Get the current span and set the span name to the current
            # function name of the request.
            tracer = _get_current_tracer()
            span = tracer.current_span()
            span.name = utils.get_func_name(view_func)
        except Exception:  # pragma: NO COVER
            logger.warning('Failed to trace request', exc_info=True)

    def process_response(self, request, response):
        # Do not trace if the url is blacklisted
        if utils.disable_tracing_url(request.path, self.blacklist_paths):
            return response

        try:
            span = _get_django_span()
            span.add_attribute(
                attribute_key=HTTP_STATUS_CODE,
                attribute_value=response.status_code)

            _set_django_attributes(span, request)

            tracer = _get_current_tracer()
            tracer.end_span()
            tracer.finish()
        except Exception:  # pragma: NO COVER
            logger.warning('Failed to trace request', exc_info=True)
        finally:
            return response
