import json
import socket

from rejected import consumer, testing
from tornado import concurrent, gen
import mock

from vetoes import service


class Consumer(service.HTTPServiceMixin,
               consumer.Consumer):

    def __init__(self, *args, **kwargs):
        kwargs['service_map'] = {'fetch-stats': 'httpbin'}
        super(Consumer, self).__init__(*args, **kwargs)
        self.method = 'GET'
        self.request_body = None
        self.request_json = None

    @gen.coroutine
    def process(self):
        yield self.call_http_service('fetch-stats', self.method, 'stats',
                                     **{'body': self.request_body,
                                        'json': self.request_json})

    def get_service_url(self, service, *path, **kwargs):
        return 'http://httpbin.org/status/200'


class HTTPServiceMixinTests(testing.AsyncTestCase):

    def setUp(self):
        super(HTTPServiceMixinTests, self).setUp()
        self.consumer.http = mock.Mock()
        self.http_response = mock.Mock(code=200, request_time=0)
        self.consumer.http.fetch.return_value = concurrent.Future()
        self.consumer.http.fetch.return_value.set_result(self.http_response)

    def get_consumer(self):
        return Consumer

    @testing.gen_test
    def test_that_sentry_context_is_managed(self):
        with mock.patch.multiple(self.consumer,
                                 set_sentry_context=mock.DEFAULT,
                                 unset_sentry_context=mock.DEFAULT) as context:
            yield self.process_message()
            context['set_sentry_context'].assert_called_once_with(
                'service_invoked', 'httpbin')
            context['unset_sentry_context'].assert_called_once_with(
                'service_invoked')

    @testing.gen_test
    def test_that_metrics_are_emitted(self):
        measurement = yield self.process_message()
        self.assertIn('http.fetch-stats.200', measurement.values)
        self.assertEqual(measurement.values['http.fetch-stats.200'],
                         self.http_response.request_time)

    @testing.gen_test
    def test_that_timeout_result_in_processing_exceptions(self):
        self.http_response.code = 599
        with self.assertRaises(consumer.ProcessingException):
            measurement = yield self.process_message()
            self.assertEqual(measurement.values['http.fetch-stats.599'],
                             self.http_response.request_time)
    @testing.gen_test
    def test_that_rate_limiting_result_in_processing_exceptions(self):
        self.http_response.code = 429
        with self.assertRaises(consumer.ProcessingException):
            measurement = yield self.process_message()
            self.assertEqual(measurement.values['http.fetch-stats.429'],
                             self.http_response.request_time)

    @testing.gen_test
    def test_that_call_http_service_accepts_body(self):
        self.consumer.method = 'POST'
        self.consumer.request_body = mock.sentinel.body
        yield self.process_message()
        self.consumer.http.fetch.assert_called_once_with(
            self.consumer.get_service_url('fetch-stats'),
            headers={'Correlation-Id': self.correlation_id},
            method='POST', body=mock.sentinel.body, raise_error=False)

    @testing.gen_test
    def test_that_call_http_service_jsonifies(self):
        self.consumer.method = 'POST'
        self.consumer.request_json = {'one': 1}
        yield self.process_message()
        self.consumer.http.fetch.assert_called_once_with(
            self.consumer.get_service_url('fetch-stats'),
            method='POST', body=json.dumps({'one': 1}).encode('utf-8'),
            headers={'Content-Type': 'application/json',
                     'Correlation-Id': self.correlation_id},
            raise_error=False)

    @testing.gen_test
    def test_that_socket_errors_result_in_processing_exception(self):
        future = concurrent.Future()
        future.set_exception(socket.error(42, 'message'))
        self.consumer.http.fetch.return_value = future

        with self.assertRaises(consumer.ProcessingException):
            yield self.process_message()
        self.assertGreater(
            self.consumer._measurement.values['http.fetch-stats.timeout'],
            self.http_response.request_time)
        self.assertEqual(
            self.consumer._measurement.counters['errors.socket.42'], 1)

    @testing.gen_test
    def test_that_raise_error_can_be_overridden(self):
        self.http_response.code = 500
        self.http_response.rethrow.side_effect = RuntimeError

        response = yield self.consumer.call_http_service(
            'fetch-stats', 'GET', raise_error=False)

        self.consumer.http.fetch.assert_called_once_with(
            self.consumer.get_service_url('fetch-stats'),
            method='GET', raise_error=False)
        self.assertIs(response, self.http_response)

    @testing.gen_test
    def test_that_url_kwarg_skips_service_lookup(self):
        with mock.patch.multiple(self.consumer,
                                 set_sentry_context=mock.DEFAULT,
                                 unset_sentry_context=mock.DEFAULT) as context:
            response = yield self.consumer.call_http_service(
                'frobinicate', 'GET', url='https://google.com')
            self.consumer.http.fetch.assert_called_once_with(
                'https://google.com', method='GET', raise_error=False)
            self.assertIs(response, self.http_response)
        context['set_sentry_context'].assert_called_once_with(
            'service_invoked', 'frobinicate')
        context['unset_sentry_context'].assert_called_once_with(
            'service_invoked')
